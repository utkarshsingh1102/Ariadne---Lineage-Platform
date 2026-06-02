"""Orchestration-layer parsers — v0.2 §7.

Each parser returns an ``OrchestrationJobIR``. The visitor / API never invokes
these directly; callers point at a DAG file / workflow JSON / shell script
explicitly.
"""
