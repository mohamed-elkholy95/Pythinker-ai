"""Channels picker step."""

from __future__ import annotations

from pythinker.cli.onboard_types import StepResult, _WizardContext


def _step_channels(ctx: _WizardContext) -> StepResult:
    """Step 12 — channel picker loop.

    Skipped in QuickStart. Manual flow:
      1) Show "How channels work" panel.
      2) Loop: pick a channel from the dynamically-discovered registry
         (telegram, discord, slack, email, matrix, msteams, whatsapp, websocket),
         show its instructions if available, then run the full per-field
         pydantic walker (`_configure_channel`) on its config.
      3) "Done" returns to the main wizard flow.

    Channels come from `pythinker.channels.registry` so the wizard never
    lags the codebase, and the per-channel editor covers every field the
    channel exposes — not just the auth token.
    """
    from pythinker.cli import onboard as _onboard
    from pythinker.cli.onboard_views import clack
    from pythinker.cli.onboard_views.panels import CHANNEL_INSTRUCTIONS, CHANNELS_INTRO

    if ctx.flow != "manual":
        return StepResult(status="skip")

    clack.note("How channels work", CHANNELS_INTRO)
    clack.bar_break()

    channel_names = _onboard._get_channel_names()  # {registry_key: display_name}
    if not channel_names:
        clack.print_status("No channels available — skipping channel setup.")
        return StepResult(status="continue")

    while True:
        options: list[tuple[str, str, str]] = []
        for name, display in channel_names.items():
            ch = getattr(ctx.draft.channels, name, None)
            enabled = bool(ch) and (
                ch.get("enabled") if isinstance(ch, dict) else getattr(ch, "enabled", False)
            )
            hint = "configured" if enabled else ""
            options.append((name, display, hint))
        options.append(("__done__", "Done — continue setup", ""))

        try:
            picked = clack.select(
                "Configure a channel",
                options=options,
                default="__done__",
                searchable=True,
            )
        except clack.WizardCancelled:
            break

        if picked == "__done__":
            break

        # When a channel is already enabled, ask the user what they want to do
        # before silently dropping into the editor — they may have just wanted
        # to disable it, or to leave it alone after re-checking.
        if _onboard._channel_is_enabled(ctx.draft, picked):
            display = channel_names.get(picked, picked.title())
            action = _onboard._prompt_configured_channel_action(display)
            if action == "skip":
                clack.bar_break()
                continue
            if action == "disable":
                _onboard._set_channel_enabled(ctx.draft, picked, False)
                clack.print_status(f"{display} disabled (config kept).")
                clack.bar_break()
                continue
            # action == "update" → fall through to the editor below.

        instr = CHANNEL_INSTRUCTIONS.get(picked)
        if instr:
            clack.note(f"{channel_names.get(picked, picked.title())} setup", instr)
            clack.bar_break()

        # Reuse the existing per-field pydantic walker; it covers
        # every field on the channel's config class (token, allowlists, polling
        # intervals, webhook URLs, …) rather than just the auth token.
        try:
            _onboard._configure_channel(ctx.draft, picked)
        except KeyboardInterrupt:
            clack.print_status(f"{channel_names.get(picked, picked)} edit cancelled.")
            continue

        clack.bar_break()

    _onboard._emit_docs_link("channels")
    return StepResult(status="continue")
