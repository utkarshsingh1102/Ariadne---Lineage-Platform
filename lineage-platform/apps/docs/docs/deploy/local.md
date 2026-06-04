---
title: Local
sidebar_label: Local
---

# Deploy → Local

See [Quick start](/overview/quick-start) for the canonical five-command
local setup.

## OS-specific notes

### macOS

Docker Desktop 4.20+. Enable "Use Rosetta for x86/amd64 emulation" if
on Apple Silicon — the parser images are amd64 and run noticeably
faster under Rosetta than under qemu.

### Linux

Docker Engine 24+ + docker-compose v2. No Docker Desktop needed.

### Windows

Use [`setup-windows.ps1`](https://github.com/utkarshsingh1102/Ariadne---Lineage-Platform/blob/main/setup-windows.ps1) — one-shot installer for WSL2 + Docker Desktop + Git.
Run it as Administrator in PowerShell:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
.\setup-windows.ps1
```

It also stages `~/setup-wsl.sh` inside Ubuntu so the second-stage
Python/Node install runs the moment WSL opens.

## See also

- [Operations](/deploy/operations) for stop / start / log tailing.
- [AWS](/deploy/aws) for cloud deployment.
