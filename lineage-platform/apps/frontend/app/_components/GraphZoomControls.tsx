"use client";

/**
 * Bottom-right zoom controls that sit just above the minimap.
 *
 * Used to live in the top-right GraphToolbar; moved here per UX feedback so
 * the top toolbar can stay focused on search + filter chips, and zoom is
 * thumb-reachable next to the minimap preview.
 */

export interface GraphZoomControlsProps {
  onZoomIn: () => void;
  onZoomOut: () => void;
  onFit: () => void;
}

export function GraphZoomControls({
  onZoomIn,
  onZoomOut,
  onFit,
}: GraphZoomControlsProps) {
  return (
    <div
      className="graph-zoom-controls"
      role="toolbar"
      aria-label="Zoom controls"
    >
      <button
        type="button"
        className="graph-zoom-controls__btn"
        onClick={onZoomOut}
        aria-label="Zoom out"
        title="Zoom out"
      >
        −
      </button>
      <button
        type="button"
        className="graph-zoom-controls__btn"
        onClick={onFit}
        aria-label="Fit to view"
        title="Fit to view"
      >
        ⊡
      </button>
      <button
        type="button"
        className="graph-zoom-controls__btn"
        onClick={onZoomIn}
        aria-label="Zoom in"
        title="Zoom in"
      >
        +
      </button>
    </div>
  );
}
