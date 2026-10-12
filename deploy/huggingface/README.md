---
title: Aawiz Voice Check-in
emoji: 🎙️
colorFrom: indigo
colorTo: blue
sdk: docker
app_port: 7860
license: apache-2.0
short_description: A private daily check-in agent on Nebius with NVIDIA Nemotron
---

# Aawiz Voice Check-in: public demo

A 30-second daily check-in agent. An NVIDIA open model on Nebius Token Factory decides which
tools to call; every tool call goes through a real MCP server over Streamable HTTP, shown live
in the "MCP activity" panel.

**This is a public demo with synthetic data. Please do not enter real personal information.**
Each visitor gets a random pseudonym with an invented 14-day history. Everything a visitor's
pseudonym holds is deleted after two hours.

Not a medical service. It does not diagnose, treat or give medical advice.

Source code (Apache-2.0): https://github.com/Aawizgroup/aawiz-voice-checkin/tree/feature/nebius-personal-ai
