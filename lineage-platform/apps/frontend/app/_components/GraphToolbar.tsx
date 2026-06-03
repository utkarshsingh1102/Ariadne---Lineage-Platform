"use client";

/**
 * Floating top-right toolbar for the lineage / explorer canvas.
 *
 * Layout (v0.3 — collapsible):
 *   - Always-visible row: filter-toggle button + search input + active-query tags
 *   - Collapsible body: type-filter chips (Attribute / Connection / DataFrame / …)
 *
 * Search is **cumulative** — each Enter adds the term as a tag below the input
 * and tells GraphCanvas to highlight every matching node in the entire graph.
 * Click the × on a tag to remove that term; nodes that only matched that term
 * lose the highlight. Highlights persist across pan/zoom and across multiple
 * searches.
 *
 * Zoom controls used to live here; they moved to the bottom-right minimap area
 * (see GraphZoomControls).
 */
import { useState } from "react";
import { getNodeDefinition } from "../_lib/node-definitions";

export interface GraphToolbarProps {
  /** Set of node-label strings currently turned ON. */
  visibleTypes: Set<string>;
  /** All node-label strings present in the payload (chip list). */
  availableTypes: string[];
  onToggleType: (label: string) => void;
  /** Active search terms; the toolbar renders one removable tag per entry. */
  searchQueries: string[];
  /** Called when the user presses Enter on a NEW term (not already in the list). */
  onAddSearch: (query: string) => void;
  /** Called when the user clicks × on a tag. */
  onRemoveSearch: (query: string) => void;
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
  searchQueries,
  onAddSearch,
  onRemoveSearch,
}: GraphToolbarProps) {
  const [query, setQuery] = useState("");
  // Filter chips are hidden by default so the toolbar is compact; the user
  // toggles them via the funnel button. Search + tag list stay visible.
  const [filtersOpen, setFiltersOpen] = useState(false);

  return (
    <div className="graph-canvas-toolbar" role="toolbar" aria-label="Graph controls">
      <div className="graph-canvas-toolbar__row">
        <button
          type="button"
          className={
            "graph-canvas-toolbar__filter-btn" +
            (filtersOpen ? " graph-canvas-toolbar__filter-btn--on" : "")
          }
          onClick={() => setFiltersOpen((v) => !v)}
          aria-expanded={filtersOpen}
          aria-label={filtersOpen ? "Hide type filters" : "Show type filters"}
          title={filtersOpen ? "Hide type filters" : "Show type filters"}
        >
          {/* Funnel icon — inline SVG to avoid an extra import */}
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none"
               stroke="currentColor" strokeWidth="2"
               strokeLinecap="round" strokeLinejoin="round">
            <polygon points="22 3 2 3 10 12.46 10 19 14 21 14 12.46 22 3" />
          </svg>
          {visibleTypes.size > 0 && filtersOpen === false && (
            <span className="graph-canvas-toolbar__filter-badge">
              {visibleTypes.size}
            </span>
          )}
        </button>

        <form
          className="graph-canvas-toolbar__search"
          onSubmit={(e) => {
            e.preventDefault();
            const q = query.trim();
            if (!q) return;
            // Don't add duplicates; just clear the input.
            if (!searchQueries.includes(q)) onAddSearch(q);
            setQuery("");
          }}
        >
          <input
            type="search"
            placeholder="Find a node…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            aria-label="Search nodes (press Enter to add a highlight)"
          />
        </form>
      </div>

      {searchQueries.length > 0 && (
        <div className="graph-canvas-toolbar__tags" role="list" aria-label="Active searches">
          {searchQueries.map((q) => (
            <span key={q} className="graph-canvas-toolbar__tag" role="listitem">
              <span className="graph-canvas-toolbar__tag-text">{q}</span>
              <button
                type="button"
                className="graph-canvas-toolbar__tag-x"
                onClick={() => onRemoveSearch(q)}
                aria-label={`Remove search term ${q}`}
                title={`Remove "${q}"`}
              >
                ×
              </button>
            </span>
          ))}
        </div>
      )}

      {filtersOpen && availableTypes.length > 0 && (
        <div className="graph-canvas-toolbar__group" aria-label="Type filters">
          {availableTypes.map((t) => {
            const active = visibleTypes.has(t);
            const def = getNodeDefinition(t);
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
    </div>
  );
}
