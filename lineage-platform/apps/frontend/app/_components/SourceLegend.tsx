"use client";

import {
  SOURCE_SYSTEM_BG,
  SOURCE_SYSTEM_COLORS,
} from "../_lib/cytoscape-config";

const ENTRIES: { key: string; label: string }[] = [
  { key: "tableau", label: "Tableau" },
  { key: "qlikview", label: "QlikView" },
  { key: "tws", label: "TWS" },
  { key: "spark", label: "Spark" },
  { key: "shared", label: "Shared (Tables, Connections)" },
  { key: "unknown", label: "Unknown" },
];

// Swatches mirror how nodes actually paint in cytoscape — light tint fill
// inside a saturated 2 px border. A solid colour chip would mislead the
// eye since the canvas never shows that flat colour.
export function SourceLegend() {
  return (
    <div className="legend" aria-label="Source system legend">
      {ENTRIES.map(({ key, label }) => (
        <span key={key}>
          <span
            className="legend__swatch"
            style={{
              background: SOURCE_SYSTEM_BG[key],
              borderColor: SOURCE_SYSTEM_COLORS[key],
            }}
          />
          {label}
        </span>
      ))}
    </div>
  );
}