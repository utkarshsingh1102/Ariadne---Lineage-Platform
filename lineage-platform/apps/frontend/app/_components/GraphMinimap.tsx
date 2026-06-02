"use client";

/**
 * Lightweight minimap overlay for the graph canvas.
 *
 * Renders a scaled-down rectangle for every node plus a viewport indicator
 * that follows the parent cytoscape's pan/zoom. Drag the indicator to pan;
 * click to center.
 *
 * Pure canvas — no extra DOM nodes per graph node, so the cost stays flat
 * even at 500+ nodes.
 */
import { useEffect, useRef } from "react";
import type { Core } from "cytoscape";

export interface GraphMinimapProps {
  cy: Core | null;
  width?: number;
  height?: number;
}

const PAD = 8;

export function GraphMinimap({ cy, width = 200, height = 140 }: GraphMinimapProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  // Redraw whenever the parent cytoscape changes shape, viewport, or content.
  useEffect(() => {
    if (!cy) return;
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    let raf = 0;
    const schedule = () => {
      if (raf) return;
      raf = requestAnimationFrame(() => {
        raf = 0;
        draw();
      });
    };

    const draw = () => {
      const W = canvas.width;
      const H = canvas.height;
      ctx.clearRect(0, 0, W, H);

      // Find the bbox of all nodes in graph coords.
      const nodes = cy.nodes().filter((n) => n.visible());
      if (nodes.length === 0) return;
      let minX = Infinity;
      let minY = Infinity;
      let maxX = -Infinity;
      let maxY = -Infinity;
      nodes.forEach((n) => {
        const bb = n.boundingBox({ includeLabels: false });
        if (bb.x1 < minX) minX = bb.x1;
        if (bb.y1 < minY) minY = bb.y1;
        if (bb.x2 > maxX) maxX = bb.x2;
        if (bb.y2 > maxY) maxY = bb.y2;
      });
      const graphW = Math.max(1, maxX - minX);
      const graphH = Math.max(1, maxY - minY);
      const usableW = W - PAD * 2;
      const usableH = H - PAD * 2;
      const scale = Math.min(usableW / graphW, usableH / graphH);
      const offsetX = PAD + (usableW - graphW * scale) / 2;
      const offsetY = PAD + (usableH - graphH * scale) / 2;
      const project = (x: number, y: number) => ({
        x: offsetX + (x - minX) * scale,
        y: offsetY + (y - minY) * scale,
      });

      // Backdrop.
      ctx.fillStyle = "#f4f4f4";
      ctx.fillRect(0, 0, W, H);
      ctx.strokeStyle = "#c6c6c6";
      ctx.strokeRect(0.5, 0.5, W - 1, H - 1);

      // Nodes.
      nodes.forEach((n) => {
        const bb = n.boundingBox({ includeLabels: false });
        const p = project(bb.x1, bb.y1);
        const w = (bb.x2 - bb.x1) * scale;
        const h = (bb.y2 - bb.y1) * scale;
        ctx.fillStyle = "#525252";
        ctx.fillRect(p.x, p.y, Math.max(1, w), Math.max(1, h));
      });

      // Viewport rectangle.
      const extent = cy.extent();
      const v1 = project(extent.x1, extent.y1);
      const v2 = project(extent.x2, extent.y2);
      ctx.strokeStyle = "#0f62fe";
      ctx.lineWidth = 1.5;
      ctx.strokeRect(v1.x, v1.y, v2.x - v1.x, v2.y - v1.y);
      ctx.fillStyle = "rgba(15, 98, 254, 0.08)";
      ctx.fillRect(v1.x, v1.y, v2.x - v1.x, v2.y - v1.y);
    };

    cy.on("render viewport pan zoom add remove", schedule);
    schedule();
    return () => {
      cy.off("render viewport pan zoom add remove", schedule);
      if (raf) cancelAnimationFrame(raf);
    };
  }, [cy]);

  // Click / drag to pan.
  const dragRef = useRef<{ active: boolean }>({ active: false });
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !cy) return;
    const onDown = (e: MouseEvent) => {
      dragRef.current.active = true;
      panToClick(e);
    };
    const onMove = (e: MouseEvent) => {
      if (!dragRef.current.active) return;
      panToClick(e);
    };
    const onUp = () => {
      dragRef.current.active = false;
    };
    const panToClick = (e: MouseEvent) => {
      const rect = canvas.getBoundingClientRect();
      const px = e.clientX - rect.left;
      const py = e.clientY - rect.top;
      const W = canvas.width;
      const H = canvas.height;
      const nodes = cy.nodes().filter((n) => n.visible());
      if (nodes.length === 0) return;
      let minX = Infinity;
      let minY = Infinity;
      let maxX = -Infinity;
      let maxY = -Infinity;
      nodes.forEach((n) => {
        const bb = n.boundingBox({ includeLabels: false });
        if (bb.x1 < minX) minX = bb.x1;
        if (bb.y1 < minY) minY = bb.y1;
        if (bb.x2 > maxX) maxX = bb.x2;
        if (bb.y2 > maxY) maxY = bb.y2;
      });
      const graphW = Math.max(1, maxX - minX);
      const graphH = Math.max(1, maxY - minY);
      const usableW = W - PAD * 2;
      const usableH = H - PAD * 2;
      const scale = Math.min(usableW / graphW, usableH / graphH);
      const offsetX = PAD + (usableW - graphW * scale) / 2;
      const offsetY = PAD + (usableH - graphH * scale) / 2;
      const gx = minX + (px - offsetX) / scale;
      const gy = minY + (py - offsetY) / scale;
      cy.center({ x: gx, y: gy } as any);
    };
    canvas.addEventListener("mousedown", onDown);
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => {
      canvas.removeEventListener("mousedown", onDown);
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
  }, [cy]);

  return (
    <div className="graph-minimap" aria-label="Graph minimap">
      <canvas
        ref={canvasRef}
        width={width}
        height={height}
        style={{ width: `${width}px`, height: `${height}px` }}
      />
    </div>
  );
}
