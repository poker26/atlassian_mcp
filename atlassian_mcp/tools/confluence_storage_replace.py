"""Server-side partial updates to Confluence page body.storage (safe replace)."""
from __future__ import annotations

import copy
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from typing import Any

from requests import HTTPError

from atlassian_mcp.clients import confluence
from atlassian_mcp.config import settings
from atlassian_mcp.tools.common import (
    StructuredToolError,
    ToolError,
    safe_call,
    sanitize_str,
    sanitize_strings,
)

log = logging.getLogger(__name__)


def _base() -> str:
    return confluence.url.rstrip("/")


def put_confluence_content_update(
    current_page: dict[str, Any],
    body_value: str,
    representation: str,
    minor_edit: bool,
    version_comment: str | None,
) -> dict[str, Any]:
    """PUT ``rest/api/content/{id}`` with a new body and ``version.number = current+1``.

    Raises:
        StructuredToolError: ``VERSION_CONFLICT`` on HTTP 409 from Confluence.
        ToolError: on unexpected payloads or other HTTP failures.
    """
    page_id = current_page.get("id")
    current_version = (current_page.get("version") or {}).get("number")
    if not page_id or not isinstance(current_version, int):
        raise ToolError(
            f"Cannot build content PUT — missing id or numeric version in page "
            f"payload (id={page_id!r}, version={current_version!r})."
        )
    next_version = current_version + 1
    title = current_page.get("title") or ""
    space_key = (current_page.get("space") or {}).get("key")

    metadata = copy.deepcopy(current_page.get("metadata") or {})
    if not isinstance(metadata.get("properties"), dict):
        metadata["properties"] = {}
    properties = metadata["properties"]
    if "content-appearance-draft" not in properties:
        properties["content-appearance-draft"] = {"value": "fixed-width"}
    if "content-appearance-published" not in properties:
        properties["content-appearance-published"] = {"value": "fixed-width"}

    version_block: dict[str, Any] = {
        "number": next_version,
        "minorEdit": minor_edit,
    }
    if version_comment:
        version_block["message"] = version_comment

    if representation not in ("storage", "wiki"):
        raise ToolError(
            f"put_confluence_content_update only supports representation "
            f"'storage' or 'wiki' (got {representation!r})."
        )
    body_payload = {
        representation: {
            "value": body_value,
            "representation": representation,
        }
    }

    payload: dict[str, Any] = {
        "id": str(page_id),
        "type": current_page.get("type", "page"),
        "title": title,
        "version": version_block,
        "metadata": metadata,
        "body": body_payload,
    }
    if space_key:
        payload["space"] = {"key": space_key}

    try:
        result = confluence.put(
            f"rest/api/content/{page_id}",
            data=payload,
            params={"status": "current"},
        )
    except HTTPError as http_error:
        response = http_error.response
        status = response.status_code if response is not None else None
        if status == 409:
            raise StructuredToolError(
                "VERSION_CONFLICT",
                "Confluence rejected the update because the content version no "
                "longer matches the expected target. Re-fetch the page with "
                "confluence_get_page and retry.",
                {
                    "http_status": status,
                    "page_id": str(page_id),
                    "hint": "confluence_get_page(page_id)",
                },
            ) from http_error
        log.exception(
            "Confluence content PUT failed page_id=%s http_status=%s",
            page_id,
            status,
        )
        raise ToolError(
            f"Confluence content PUT failed (HTTP {status}): {http_error}"
        ) from http_error
    except Exception:
        log.exception("Confluence content PUT failed page_id=%s", page_id)
        raise

    if not isinstance(result, dict):
        raise ToolError(f"Unexpected PUT response type: {type(result).__name__}")
    return result


def _validate_storage_after_patch(storage: str) -> None:
    """Structural validation of HTML-ish storage after edits (relaxed parser).

    Confluence ``ac:`` macro markup is not reliably parseable with a strict
    HTML parser; ``recover=True`` still catches catastrophic garbage while
    accepting typical Data Center storage bodies.
    """
    from lxml.html import HTMLParser, fragments_fromstring

    parser = HTMLParser(recover=True)
    try:
        fragments_fromstring(storage, parser=parser)
    except Exception as parse_error:
        raise StructuredToolError(
            "INVALID_STORAGE_AFTER_PATCH",
            "Patched body.storage is not parseable as HTML fragments. "
            "Undo via page history or fix the replacement patterns.",
            {"parse_error": str(parse_error)},
        ) from parse_error


def _run_regex_subn_with_timeout(
    compiled: re.Pattern[str],
    replacement_template: str,
    text: str,
    count_limit: int,
    timeout_seconds: float,
) -> tuple[str, int]:
    """Run ``re.subn`` in a worker thread with a wall-clock timeout."""

    def run_subn() -> tuple[str, int]:
        return compiled.subn(replacement_template, text, count=count_limit)

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(run_subn)
        try:
            return future.result(timeout=timeout_seconds)
        except FuturesTimeoutError as timeout_error:
            raise StructuredToolError(
                "REGEX_TIMEOUT",
                f"Regex replacement exceeded {timeout_seconds}s wall time.",
                {"timeout_seconds": timeout_seconds},
            ) from timeout_error


def _normalize_replacement_rules(
    replacements: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not replacements:
        raise ToolError("replacements must be a non-empty list of rule objects.")
    if len(replacements) > settings.replace_max_rules:
        raise ToolError(
            f"Too many replacement rules ({len(replacements)}). "
            f"Maximum is {settings.replace_max_rules}."
        )

    normalized: list[dict[str, Any]] = []
    combined_raw = 0
    for index, raw_rule in enumerate(replacements):
        if not isinstance(raw_rule, dict):
            raise ToolError(f"replacements[{index}] must be an object, not {type(raw_rule).__name__}.")
        find = raw_rule.get("find")
        replace = raw_rule.get("replace")
        match_mode = str(raw_rule.get("match", "literal")).lower().strip()
        max_occurrences = raw_rule.get("max_occurrences")

        if not isinstance(find, str) or not isinstance(replace, str):
            raise ToolError(
                f"replacements[{index}] requires string fields 'find' and 'replace'."
            )
        if match_mode not in ("literal", "regex"):
            raise ToolError(
                f"replacements[{index}].match must be 'literal' or 'regex' "
                f"(got {match_mode!r})."
            )
        if find == "":
            raise ToolError(f"replacements[{index}].find must not be an empty string.")
        if len(find) > settings.replace_max_find_length:
            raise ToolError(
                f"replacements[{index}].find exceeds max length "
                f"{settings.replace_max_find_length} bytes."
            )
        if len(replace) > settings.replace_max_replace_length:
            raise ToolError(
                f"replacements[{index}].replace exceeds max length "
                f"{settings.replace_max_replace_length} bytes."
            )
        combined_raw += len(find.encode("utf-8")) + len(replace.encode("utf-8"))
        if combined_raw > settings.replace_max_combined_find_replace_bytes:
            raise ToolError(
                "Combined UTF-8 size of all find+replace strings exceeds "
                f"{settings.replace_max_combined_find_replace_bytes} bytes."
            )

        max_occ_int: int | None
        if max_occurrences is None:
            max_occ_int = None
        elif isinstance(max_occurrences, int) and max_occurrences > 0:
            max_occ_int = max_occurrences
        else:
            raise ToolError(
                f"replacements[{index}].max_occurrences must be a positive integer "
                f"or omitted (got {max_occurrences!r})."
            )

        normalized.append({
            "find": find,
            "replace": replace,
            "match": match_mode,
            "max_occurrences": max_occ_int,
            "index": index,
        })
    return normalized


def _apply_literal_replacement(
    text: str,
    find: str,
    replace: str,
    max_occurrences: int | None,
    global_cap: int,
) -> tuple[str, int, int, list[str]]:
    """Apply a literal find/replace. Returns (new_text, applied, eligible_total, warnings)."""
    warnings: list[str] = []
    eligible = text.count(find)
    cap = global_cap
    if max_occurrences is not None:
        cap = min(cap, max_occurrences)
    applied = min(eligible, cap)
    if eligible > cap:
        warnings.append(
            f"literal rule matched {eligible} times but only {cap} replacements "
            f"were applied (max_occurrences / server cap)."
        )
    if applied == 0:
        return text, 0, eligible, warnings
    new_text = text.replace(find, replace, cap)
    return new_text, applied, eligible, warnings


def _apply_regex_replacement(
    text: str,
    pattern: str,
    replace: str,
    max_occurrences: int | None,
    per_rule_cap: int,
    timeout_seconds: float,
) -> tuple[str, int, int, list[str]]:
    warnings: list[str] = []
    try:
        compiled = re.compile(pattern)
    except re.error as regex_error:
        raise ToolError(f"Invalid regex pattern: {regex_error}") from regex_error

    eligible_raw = 0
    for _ in compiled.finditer(text):
        eligible_raw += 1
        if eligible_raw > per_rule_cap:
            warnings.append(
                f"regex had more than {per_rule_cap} matches; counting is truncated "
                f"at the server cap."
            )
            break

    count_limit = per_rule_cap
    if max_occurrences is not None:
        count_limit = min(count_limit, max_occurrences)

    if eligible_raw == 0:
        return text, 0, 0, warnings

    if eligible_raw > count_limit:
        warnings.append(
            f"regex matched {eligible_raw} times; only {count_limit} replacements "
            f"will be applied."
        )

    new_text, applied = _run_regex_subn_with_timeout(
        compiled,
        replace,
        text,
        count_limit,
        timeout_seconds,
    )
    return new_text, applied, eligible_raw, warnings


def _snippet_around(haystack: str, index: int, radius: int) -> str:
    start = max(0, index - radius)
    end = min(len(haystack), index + radius)
    chunk = haystack[start:end]
    return " ".join(chunk.replace("\n", " ").replace("\r", " ").split())


def confluence_replace_in_page_storage(
    page_id: str,
    replacements: list[dict[str, Any]],
    minor_edit: bool = False,
    dry_run: bool = False,
    expected_version: int | None = None,
    fail_if_no_match: bool = False,
    version_comment: str | None = None,
    include_match_snippets: bool = True,
    snippet_radius: int = 48,
) -> dict[str, Any]:
    """Read body.storage from Confluence, apply find/replace rules, validate, then PUT.

    This is the preferred way for agents to edit large pages: the full storage
    HTML never travels through the MCP request beyond the affected spans.

    Each rule object supports:
      - ``find`` (string), ``replace`` (string)
      - ``match``: ``literal`` (default) or ``regex`` (``re`` syntax; subject to timeout)
      - ``max_occurrences`` (optional positive int) — cap per rule

    Args:
        page_id: Confluence page content id.
        replacements: ordered list of replacement rules.
        minor_edit: forward to Confluence ``minorEdit`` on the new version.
        dry_run: if True, do not PUT; return match counts and optional snippets.
        expected_version: if set, the tool fails with ``VERSION_CONFLICT`` unless
            the current page ``version.number`` equals this value after the GET.
        fail_if_no_match: if True, raises ``NO_MATCH`` when no replacement could
            be applied across all rules (all counts zero).
        version_comment: optional version message stored with the new version.
        include_match_snippets: when ``dry_run`` is True, include short context
            strings around the first match of each rule (from intermediate text).
        snippet_radius: half-width of each snippet in characters.

    Returns:
        Structured result with ``status`` ``ok``, ``no_op``, or ``partial`` (some
        rules matched zero times), per-rule tallies, ``warnings``, and after a
        successful write ``version_after`` / ``version_before``.
    """
    started = time.perf_counter()
    rules = _normalize_replacement_rules(replacements)

    raw_page = safe_call(
        confluence.get,
        f"rest/api/content/{page_id}",
        params={
            "expand": "body.storage,version,space,title,type,metadata.properties",
        },
    )
    if not isinstance(raw_page, dict):
        raise ToolError(f"Unexpected page payload type: {type(raw_page).__name__}")

    current_version = (raw_page.get("version") or {}).get("number")
    if not isinstance(current_version, int):
        raise ToolError(
            f"Page {page_id} has no integer version.number — cannot update safely."
        )

    if expected_version is not None and expected_version != current_version:
        raise StructuredToolError(
            "VERSION_CONFLICT",
            f"Page version is {current_version} but expected_version was "
            f"{expected_version}. Re-fetch with confluence_get_page and retry.",
            {
                "page_id": str(page_id),
                "current_version": current_version,
                "expected_version": expected_version,
                "hint": "confluence_get_page(page_id)",
            },
        )

    original_storage = (
        ((raw_page.get("body") or {}).get("storage") or {}).get("value")
    )
    if not isinstance(original_storage, str):
        raise ToolError(
            f"Page {page_id} has no body.storage.value — cannot run storage replace."
        )

    baseline_storage = sanitize_str(original_storage)
    working = baseline_storage
    per_rule_reports: list[dict[str, Any]] = []
    aggregate_warnings: list[str] = []
    total_applied = 0

    for rule in rules:
        find = rule["find"]
        replace = rule["replace"]
        match_mode = rule["match"]
        max_occurrences = rule["max_occurrences"]
        rule_index = rule["index"]
        snippets: list[str] = []

        if match_mode == "literal":
            cap = settings.replace_max_literal_occurrences_per_rule
            new_working, applied, eligible, rule_warnings = _apply_literal_replacement(
                working,
                find,
                replace,
                max_occurrences,
                cap,
            )
            if dry_run and include_match_snippets and find and applied > 0:
                idx = working.find(find)
                if idx >= 0:
                    snippets.append(_snippet_around(working, idx, snippet_radius))
        else:
            new_working, applied, eligible, rule_warnings = _apply_regex_replacement(
                working,
                find,
                replace,
                max_occurrences,
                settings.replace_max_regex_occurrences_per_rule,
                settings.replace_regex_timeout_seconds,
            )
            if dry_run and include_match_snippets and applied > 0:
                compiled = re.compile(find)
                match_object = compiled.search(working)
                if match_object is not None:
                    snippets.append(
                        _snippet_around(working, match_object.start(), snippet_radius)
                    )

        aggregate_warnings.extend(rule_warnings)
        status = "applied"
        if applied == 0:
            status = "no_match"
        elif match_mode == "regex" and eligible > applied:
            status = "partial"
        elif match_mode == "literal" and eligible > applied:
            status = "partial"

        if rule["max_occurrences"] == 1 and eligible > 1:
            aggregate_warnings.append(
                f"Rule {rule_index}: MULTIPLE_MATCH — {match_mode} matched {eligible} times "
                f"but max_occurrences=1, so only one replacement was applied."
            )

        per_rule_reports.append({
            "index": rule_index,
            "match": match_mode,
            "find_preview": find[:120] + ("…" if len(find) > 120 else ""),
            "occurrences_eligible": eligible,
            "occurrences_applied": applied,
            "status": status,
            "snippets": snippets if dry_run else [],
        })
        total_applied += applied
        working = new_working

    if fail_if_no_match and total_applied == 0:
        raise StructuredToolError(
            "NO_MATCH",
            "No replacement rules matched the current body.storage and "
            "fail_if_no_match is true.",
            {"page_id": str(page_id), "rules": len(rules)},
        )

    if total_applied == 0 or working == baseline_storage:
        overall = "no_op"
    else:
        overall = "ok"
        for report in per_rule_reports:
            if report["occurrences_applied"] == 0:
                overall = "partial"
                break

    try:
        _validate_storage_after_patch(working)
    except StructuredToolError:
        log.exception(
            "confluence_replace_in_page_storage validation failed page_id=%s",
            page_id,
        )
        raise

    duration_ms = (time.perf_counter() - started) * 1000.0
    title = raw_page.get("title")
    space_key = (raw_page.get("space") or {}).get("key")

    log.info(
        "confluence_replace_in_page_storage page_id=%s dry_run=%s "
        "total_occurrences_applied=%d rules=%d duration_ms=%.0f",
        page_id,
        dry_run,
        total_applied,
        len(rules),
        duration_ms,
    )

    base_result: dict[str, Any] = {
        "page_id": str(page_id),
        "title": sanitize_str(title) if isinstance(title, str) else title,
        "space_key": space_key,
        "version_before": current_version,
        "dry_run": dry_run,
        "status": overall,
        "total_occurrences_applied": total_applied,
        "replacements": per_rule_reports,
        "warnings": aggregate_warnings,
        "duration_ms": round(duration_ms, 2),
    }

    if dry_run:
        base_result["version_after"] = None
        base_result["url"] = f"{_base()}/pages/viewpage.action?pageId={page_id}"
        return sanitize_strings(base_result)

    if working == baseline_storage:
        base_result["version_after"] = current_version
        base_result["url"] = f"{_base()}/pages/viewpage.action?pageId={page_id}"
        base_result["message"] = (
            "No changes to body.storage — PUT skipped (idempotent no_op)."
        )
        return sanitize_strings(base_result)

    updated = put_confluence_content_update(
        raw_page,
        working,
        "storage",
        minor_edit,
        version_comment,
    )
    new_version = (updated.get("version") or {}).get("number")
    base_result["version_after"] = new_version
    base_result["url"] = f"{_base()}/pages/viewpage.action?pageId={updated.get('id')}"
    base_result["message"] = "Page body.storage updated."
    return sanitize_strings(base_result)


TOOLS = [
    confluence_replace_in_page_storage,
]
