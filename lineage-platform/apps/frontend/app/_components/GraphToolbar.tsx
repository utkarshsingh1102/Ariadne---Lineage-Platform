"use client";

/**
 * Floating top-right toolbar for the lineage / explorer canvas.
 *
 *   - Zoom in / out / fit-to-view
 *   - Type-filter chips (Attribute / Connection / DataFrame / Source / Sink, …)
 *   - Search-to-focus: type a node name, press Enter → animate-centre the
 *     match (uses the existing ``focusToken`` channel into GraphCanvas).
 *
 * The toolbar owns no canvas state — every action is a callback up to the
 * parent, which is also the source of truth for the active filter set.
 */
import { useState } from "react";
import { getNodeDefinition } from "../_lib/node-definitions";

export interface GraphToolbarProps {
  /** Set of node-label strings currently turned ON. */
  visibleTypes: Set<string>;
  /** All node-label strings present in the payload (chip list). */
  availableTypes: string[];
  onToggleType: (label: string) => void;
  onZoomIn: () => void;
  onZoomOut: () => void;
  onFit: () => void;
  /** Triggered when the user presses Enter on the search box. */
  onSearch: (query: string) => void;
}

const TYPE_LABEL: Record<string, string> = {
  Attribute: "Cols",
  DataFrame: "DFs",
  Table: "Tables",
  Connection: "Conns",
  UDF: "UDFs",
};

function chipLabel(t: string): string {
  return TYPE_LABEL[t] ?? t;
}

export function GraphToolbar({
  visibleTypes,
  availableTypes,
  onToggleType,
  onZoomIn,
  onZoomOut,
  onFit,
  onSearch,
}: GraphToolbarProps) {
  const [query, setQuery] = useState("");

  return (
    <div className="graph-canvas-toolbar" role="toolbar" aria-label="Graph controls">
      <div className="graph-canvas-toolbar__group" aria-label="Zoom">
        <button
          type="button"
          className="graph-canvas-toolbar__btn"
          onClick={onZoomOut}
          aria-label="Zoom out"
          title="Zoom out"
        >
          −
        </button>
        <button
          type="button"
          className="graph-canvas-toolbar__btn"
          onClick={onFit}
          aria-label="Fit to view"
          title="Fit to view"
        >
          ⊡
        </button>
        <button
          type="button"
          className="graph-canvas-toolbar__btn"
          onClick={onZoomIn}
          aria-label="Zoom in"
          title="Zoom in"
        >
          +
        </button>
      </div>

      {availableTypes.length > 0 && (
        <div className="graph-canvas-toolbar__group" aria-label="Type filters">
          {availableTypes.map((t) => {
            const active = visibleTypes.has(t);
            const def = getNodeDefinition(t);
            // Each chip is wrapped in a positioned span so the popover
            // can anchor to it without touching the toolbar's flex layout.
            // The popover is CSS-driven (show on :hover / :focus-within)
            // so no React state is needed for the visibility toggle.
            return (
              <span key={t} className="graph-canvas-toolbar__chip-wrap">
                <button
                  type="button"
                  className={
                    "graph-canvas-toolbar__chip" +
                    (active ? " graph-canvas-toolbar__chip--on" : "")
                  }
                  onClick={() => onToggleType(t)}
                  aria-pressed={active}
                  aria-describedby={`chip-def-${t}`}
                >
                  {chipLabel(t)}
                </button>
                <div
                  id={`chip-def-${t}`}
                  className="graph-canvas-toolbar__chip-def"
                  role="tooltip"
                >
                  <div className="graph-canvas-toolbar__chip-def-head">
                    <span className="graph-canvas-toolbar__chip-def-label">
                      {def.label}
                    </span>
                    <span
                      className={
                        "graph-canvas-toolbar__chip-def-system " +
                        "graph-canvas-toolbar__chip-def-system--" + def.system
                      }
                    >
                      {def.system}
                    </span>
                  </div>
                  <p className="graph-canvas-toolbar__chip-def-body">
                    {def.summary}
                  </p>
                  <p className="graph-canvas-toolbar__chip-def-hint">
                    Click to {active ? "hide" : "show"} {def.label} nodes
                  </p>
                </div>
              </span>
            );
          })}
        </div>
      )}

      <form
        className="graph-canvas-toolbar__search"
        onSubmit={(e) => {
          e.preventDefault();
          if (query.trim()) onSearch(query.trim());
        }}
      >
        <input
          type="search"
          placeholder="Find a node…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          aria-label="Search nodes"
        />
      </form>
    </div>
  );
}
