# V7 Autonomous GUI + Software Development Agent

V7 is the autonomous layer built on the V6 foundation. Its target is not a one-shot click bot, but a bounded continuous loop that can observe software, build an evidence model, create a replica, run it, test it, diagnose failures, and iterate.

## Core loop
`OBSERVE → STATE → DECIDE → ACT → OBSERVE AGAIN → VERIFY → CONTINUE / RECOVER / STOP`

Development loop:
`OBSERVE EVIDENCE → MODEL → BUILD → RUN → TEST → DIAGNOSE → PATCH → RETEST`

## Ordinary web-GPT policy
The ordinary web GPT is **not** the default GUI controller.

- GUI fast path: **0 web-GPT calls by default**.
- Local observation, window selection, activation, OCR, safety checks, page fingerprinting and navigation bookkeeping are local.
- GPT escalation is opt-in through an explicit budget.
- GPT receives **text evidence only**: task, state, OCR text, browser text/elements, navigation facts, file names/source snippets, and test output.
- Screenshot paths, image data, `data:image`, and base64 image payloads are rejected by the reasoning interface.
- GPT is asked for the smallest verifiable next step, not unrestricted control.
- Reasoned file changes use a strict `WRITE_FILE` JSON contract and are confined to the generated project root.

## GUI autonomy
- Compound GUI goals always observe first.
- A successful tool call is never task completion by itself.
- `WINDOW_ACTIVATE` is followed by active-window verification and a fresh observation.
- Explicit target text cannot bypass the initial observation stage.
- Every business interaction is followed by a fresh observation.
- GUI pages are fingerprinted and de-duplicated.
- Navigation edges are recorded and can be marked verified.
- Target clicks require observed text evidence; no blind click.
- Irreversible business actions are blocked by policy.
- Exploration is bounded and avoids arbitrary OCR clicking.

## Autonomous development
The development loop is deliberately local-first:

1. Build from observed evidence.
2. Start and smoke-test the generated application.
3. Inspect the failure as text.
4. Apply deterministic repairs when possible.
5. If still ambiguous, optionally ask ordinary web GPT using a text-only prompt.
6. Apply only a constrained file patch returned by the reasoner.
7. Rebuild and retest.
8. Stop only on verified success or a bounded, explicit failure.

This gives the architecture needed for continuous software work without spending image quota on every GUI step.

## Install

From the existing V6 checkout:

```bash
./install_v7.sh
```

The installer backs up an existing `v7/`, compiles the installed package, runs the V7 test suite, and installs:

```text
~/ai_agent/bin/194720-v7
```

The existing V6 `194720` entrypoint is not modified.

## Run

```bash
~/ai_agent/bin/194720-v7 "请自主观察当前电脑，打开钉钉，找到同心共育并观察"
```

By default this runs with zero ordinary web-GPT calls during GUI control. An external integration can inject a text-only reasoner and an explicit development budget when higher-level reasoning is actually required.

## V7 v5 stability hardening
- Development patches are persistent: the loop never blindly rebuilds over a successful GPT/local patch.
- Repeated identical failures are bounded to prevent runaway loops and wasted GPT calls.
- Replica creation is evidence-gated: no confirmed target, no fabricated development project.
- The ordinary web-GPT path remains text-only and budgeted; GUI screenshots are never part of the reasoning contract.
- Installer is tested in an isolated `AI_AGENT_ROOT` with an existing V7 directory and backup path.


本版新增硬化：点击前后窗口身份/几何复核、唯一截图证据名、长运行旧状态隔离、仿制服务可配置端口、紧凑 wmctrl 测试格式兼容。

## Release
V7 sealed release: 晨睿｜7.0.0-sealed.14

本版额外加入横向（模块之间）与纵向（动作前→动作后）证据交叉校验：坐标必须同时满足水平锚点、垂直行一致性、邻近 OCR 冲突检查；长运行测试可使用短时限进行边界验证；安装测试禁止向发布目录写入 Python 字节码。


## V7 sealed.8 consistency hardening
- Observation is transactional: pixel capture is cross-checked against current window identity and geometry; unstable captures are retried rather than mixed with stale metadata.
- Desktop-wide observation rechecks the active window after root capture.
- Fuzzy OCR fallback requires confidence >= 60.
- After a verified target action, the controller does not perform unsolicited follow-up clicks.
- Long-running checkpoints flush and fsync before atomic replacement.
- Release validation covers regression, horizontal/vertical geometry, and longitudinal state consistency.

Release: 晨睿｜7.0.0-sealed.14


## Deep audit

V7 sealed.9 uses a 400-item audit matrix with layered single-item, module, positive, negative, horizontal, vertical, cross-module, installation and release checks. Unknown or ambiguous GUI evidence is treated as non-success. Real GUI and long-duration acceptance remain explicitly distinguished from automated tests.


## V7 sealed.13 hardening
- Added an evidence-gated WeChat contact workflow: search only when a visible search affordance exists, type only after explicit input-box evidence, re-observe after contact search/selection, and verify the typed message from fresh OCR before continuing.
- Added conservative input-anchor discovery; empty/ambiguous regions are never treated as text fields.
- Added semantic click boundaries for known Chinese UI labels including `公众号`, preventing midpoint clicks from drifting into neighboring controls.
- Added regression coverage for WeChat parsing, input-anchor safety, message-field preference, and semantic boundaries.

## V7 sealed.10 hardening
- Persisted supervisor state is bound to a logical task_id; stale state cannot silently resume a different task.
- Consecutive recovery budget resets only after verified worker progress.
- Added explicit evidence verification helpers for stable observations, same-window identity, and expected destination markers.
- Unknown or unstable evidence remains non-success.
