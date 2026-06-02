"use client";

import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
} from "react";
import Prism from "prismjs";
import "prismjs/components/prism-python";
// XML / markup grammar — lights up .twb workbooks in the source viewer.
// The gateway already returns ``language: "xml"`` for .twb / .twbx files;
// without this import the panel falls back to plain-text rendering.
import "prismjs/components/prism-markup";
import { Button, InlineLoading, Tag } from "@carbon/react";
import { Close, DocumentDownload } from "@carbon/icons-react";
import { api, type FileSourceResponse } from "../_lib/api";
import type { LineIndex, NodeRange } from "../_lib/line_index";

/**
 * Source-code viewer with two-way linking to graph nodes.
 *
 * Imperative API (exposed via ref):
 *   - ``scrollToRange(range, opts)``  — center the requested line span,
 *     apply a sticky highlight class to every line in the span, and pulse
 *     the highlight once for a quick visual cue.
 *   - ``clear()`` — drop any active highlight.
 *
 * Two-way:
 *   - Forward: the parent calls ``scrollToRange`` whenever a graph node
 *     is tapped, deriving the range from the node's ``line_range``
 *     (or ``line`` for sources/sinks).
 *   - Reverse: clicking a line in the gutter fires
 *     ``onLineClick(line, nodeIds)`` so the parent can select / center
 *     the matching node in the graph.
 *
 * Source highlighting uses Prism's Python grammar applied line-by-line —
 * that keeps the line numbers, the highlight overlay, and the tokenised
 * text in lockstep with no virtualisation pitfalls.
 */
export interface SourceCodePanelHandle {
  scrollToRange: (
    range: { start: number; end: number; step?: number | null },
    opts?: { pulse?: boolean },
  ) => void;
  clear: () => void;
}

export interface SourceCodePanelProps {
  source: "spark" | "tableau" | "qlikview";
  fileId: string | null;
  lineIndex: LineIndex;
  onClose: () => void;
  onLineClick?: (line: number, nodeIds: string[]) => void;
}

export const SourceCodePanel = forwardRef<
  SourceCodePanelHandle,
  SourceCodePanelProps
>(function SourceCodePanel(
  { source, fileId, lineIndex, onClose, onLineClick },
  ref,
) {
  const [data, setData] = useState<FileSourceResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [activeRange, setActiveRange] = useState<{
    start: number;
    end: number;
    step?: number | null;
  } | null>(null);
  const [pulseToken, setPulseToken] = useState(0);

  const scrollRef = useRef<HTMLDivElement | null>(null);

  // Fetch source whenever the script identity changes. Reset error / data
  // between fetches so a stale frame can't linger after a failure.
  useEffect(() => {
    if (!fileId) {
      setData(null);
      setError(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    api
      .fileSource(source, fileId)
      .then((res) => {
        if (cancelled) return;
        setData(res);
      })
      .catch((e: any) => {
        if (cancelled) return;
        setError(e?.message ?? String(e));
        setData(null);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [fileId, source]);

  // Pre-tokenise the file once per source change. Splitting on newlines
  // BEFORE highlighting keeps every line's tokens self-contained — strings
  // and triple-quotes that span lines are reasonable casualties; tradeoff
  // worth taking for the simpler line-by-line DOM model.
  const lines = useMemo<string[]>(() => {
    if (!data) return [];
    return data.source_code.split(/\r?\n/);
  }, [data]);

  const highlightedLines = useMemo<string[]>(() => {
    if (lines.length === 0) return [];
    const grammar =
      Prism.languages[
        (data?.language ?? "python") as keyof typeof Prism.languages
      ] ?? Prism.languages.python;
    if (!grammar) return lines.map(escapeHtml);
    return lines.map((line) =>
      line === "" ? "" : Prism.highlight(line, grammar, data?.language ?? "python"),
    );
  }, [lines, data?.language]);

  // Imperative scroll/highlight. Center the midpoint of the requested
  // range so a long span is biased toward the user's eye line.
  useImperativeHandle(
    ref,
    () => ({
      scrollToRange(range, opts) {
        if (!range) return;
        setActiveRange({
          start: range.start,
          end: range.end,
          step: range.step ?? null,
        });
        if (opts?.pulse !== false) {
          setPulseToken((p) => p + 1);
        }
        // Defer to the next tick so React has rendered the highlight
        // class before we scroll.
        requestAnimationFrame(() => {
          const target = scrollRef.current?.querySelector<HTMLElement>(
            `[data-line="${range.start}"]`,
          );
          if (target) {
            target.scrollIntoView({
              behavior: "smooth",
              block: "center",
            });
          }
        });
      },
      clear() {
        setActiveRange(null);
      },
    }),
    [],
  );

  const inRange = useCallback(
    (line: number) => {
      if (!activeRange) return false;
      return line >= activeRange.start && line <= activeRange.end;
    },
    [activeRange],
  );

  const handleGutterClick = useCallback(
    (line: number) => {
      const ids = lineIndex.lineToNodes.get(line) ?? [];
      if (ids.length > 0 && onLineClick) {
        onLineClick(line, ids);
      }
    },
    [lineIndex, onLineClick],
  );

  const downloadSource = useCallback(() => {
    if (!data) return;
    const blob = new Blob([data.source_code], { type: "text/plain" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = (data.name ?? data.file_path.split("/").pop() ?? "source.txt") || "source.txt";
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  }, [data]);

  const lineNumberWidth =
    Math.max(2, String(lines.length || 1).length) * 8 + 16; // px

  return (
    <div className="source-panel" aria-label="Source code viewer">
      <div className="source-panel__header">
        <div className="source-panel__header-text">
          <h4>{data?.name ?? (fileId ? "Source" : "View Source")}</h4>
          {data && (
            <div className="source-panel__meta">
              <Tag type="cool-gray" size="sm">
                {data.language}
              </Tag>
              <Tag type="cool-gray" size="sm">
                {data.line_count} lines
              </Tag>
              {data.truncated && (
                <Tag type="red" size="sm">
                  truncated
                </Tag>
              )}
              <span className="source-panel__path" title={data.file_path}>
                {data.file_path}
              </span>
            </div>
          )}
        </div>
        <div className="source-panel__actions">
          <Button
            kind="ghost"
            size="sm"
            renderIcon={DocumentDownload}
            hasIconOnly
            iconDescription="Download source"
            tooltipPosition="bottom"
            disabled={!data}
            onClick={downloadSource}
          />
          <Button
            kind="ghost"
            size="sm"
            renderIcon={Close}
            hasIconOnly
            iconDescription="Hide source"
            tooltipPosition="bottom"
            onClick={onClose}
          />
        </div>
      </div>

      <div className="source-panel__body" ref={scrollRef}>
        {loading && (
          <div className="source-panel__state">
            <InlineLoading description="Loading source…" />
          </div>
        )}
        {error && (
          <div className="source-panel__state source-panel__state--error">
            <strong>Failed to load source</strong>
            <div>{error}</div>
          </div>
        )}
        {!loading && !error && !fileId && (
          <div className="source-panel__state">
            <p>
              Run a lineage trace, then click a node to reveal the script
              that produced it.
            </p>
          </div>
        )}
        {!loading && !error && data && (
          <pre
            className={`source-panel__code language-${data.language}`}
            // key bumps each pulse so the CSS animation restarts even on
            // the same line range.
            key={`pulse-${pulseToken}`}
          >
            <code>
              {highlightedLines.map((html, i) => {
                const ln = i + 1;
                const highlighted = inRange(ln);
                const stepLine = activeRange?.step ?? null;
                const isStepLine =
                  stepLine !== null && stepLine !== undefined && ln === stepLine;
                const owners = lineIndex.lineToNodes.get(ln);
                const hasOwner = !!owners && owners.length > 0;
                return (
                  <div
                    key={ln}
                    data-line={ln}
                    className={[
                      "source-panel__line",
                      highlighted ? "source-panel__line--hl" : "",
                      isStepLine ? "source-panel__line--step" : "",
                      highlighted && ln === activeRange?.start
                        ? "source-panel__line--pulse"
                        : "",
                      hasOwner ? "source-panel__line--has-owner" : "",
                    ]
                      .filter(Boolean)
                      .join(" ")}
                  >
                    <button
                      type="button"
                      className="source-panel__lineno"
                      style={{ width: lineNumberWidth }}
                      onClick={() => handleGutterClick(ln)}
                      title={
                        hasOwner
                          ? `Focus ${owners!.length} node${
                              owners!.length === 1 ? "" : "s"
                            } on this line`
                          : undefined
                      }
                      tabIndex={hasOwner ? 0 : -1}
                    >
                      {ln}
                    </button>
                    <span
                      className="source-panel__line-text"
                      dangerouslySetInnerHTML={{ __html: html || "&nbsp;" }}
                    />
                  </div>
                );
              })}
            </code>
          </pre>
        )}
      </div>
    </div>
  );
});

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

/** Compute a {start,end[,step]} range for a graph node from its raw
 *  properties. Returns null when the node has no line information. */
export function rangeForNode(
  ranges: Map<string, NodeRange>,
  nodeId: string,
  stepLine?: number | null,
): { start: number; end: number; step?: number | null } | null {
  const r = ranges.get(nodeId);
  if (!r) return null;
  return { start: r.start, end: r.end, step: stepLine ?? null };
}
