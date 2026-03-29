def build_action_plan(state: dict, structured_diagnosis: dict, issue_metadata: dict) -> dict:
    diagnosis = structured_diagnosis.get("most_likely_cause", "unknown")
    facts = state.get("facts", {})

    default_plan = {
        "primary_action": "Continue monitoring current playback telemetry.",
        "recommended_actions": ["Continue monitoring current playback telemetry."],
        "next_checks": ["Recheck session state, upload behavior, and buffering telemetry on the next run."],
        "escalate_if": ["More sessions become affected or a confirmed issue persists."],
        "success_signals": ["Playback remains stable and no confirmed buffering appears."],
    }

    plans = {
        "none_detected": {
            "primary_action": "No immediate action is needed.",
            "recommended_actions": [
                "No immediate action is needed.",
            ],
            "next_checks": [
                "Watch for confirmed buffering telemetry.",
            ],
            "escalate_if": [
                "Buffering becomes confirmed.",
            ],
            "success_signals": [
                "No sessions enter confirmed buffering.",
            ],
        },
        "client_network_path_sensitivity": {
            "primary_action": "Test the same content on another client or device.",
            "recommended_actions": [
                "Test the same content on another client or device.",
                "Check network stability on the affected client path, including Wi-Fi if relevant.",
                "Lower playback quality on the affected client only as a diagnostic test.",
            ],
            "next_checks": [
                "Compare affected session bandwidth against expected bitrate.",
                "Check whether other clients continue playing the same or similar content normally.",
            ],
            "escalate_if": [
                "More sessions become affected.",
                "The same client repeatedly fails across multiple titles.",
                "Non-sensitive clients begin showing the same symptoms.",
            ],
            "success_signals": [
                "The affected session resumes stable playback.",
                "Another client continues playing fine.",
                "Lower-quality playback stabilizes on the affected client.",
            ],
        },
        "client_file_compatibility_issue": {
            "primary_action": "Test the same file on an alternate client.",
            "recommended_actions": [
                "Test the same file on an alternate client.",
                "Inspect container, subtitle type, and audio codec for the affected session.",
                "Compare the failing title with a known-good format on the same client.",
            ],
            "next_checks": [
                "Check whether MOV_TEXT subtitles, AC3 audio, or MP4 container traits are present.",
                "Verify whether the issue reproduces on the same client with similar files.",
            ],
            "escalate_if": [
                "The same title fails across multiple clients.",
                "Multiple titles with similar file traits fail on the same client type.",
            ],
            "success_signals": [
                "Playback stabilizes on an alternate client.",
                "An alternate version of the media plays cleanly.",
                "The issue is reproducible only on a specific client/file combination.",
            ],
        },
        "upload_saturation": {
            "primary_action": "Reduce simultaneous remote demand if possible.",
            "recommended_actions": [
                "Reduce simultaneous remote demand if possible.",
                "Identify competing non-Plex upload traffic.",
                "Reduce bitrate caps or high-bitrate remote sessions if needed.",
            ],
            "next_checks": [
                "Verify that upload remains near capacity over time, not just in spikes.",
                "Check whether multiple sessions are buffering at once.",
            ],
            "escalate_if": [
                "Multiple sessions continue buffering.",
                "Headroom remains critically low.",
                "Sustained saturation persists across repeated checks.",
            ],
            "success_signals": [
                "Average upload drops below the warning threshold.",
                "Upload headroom increases.",
                "Buffering clears across affected sessions.",
            ],
        },
        "transcoding": {
            "primary_action": "Inspect which sessions are transcoding.",
            "recommended_actions": [
                "Inspect which sessions are transcoding.",
                "Identify subtitle, audio, or client codec mismatches driving transcoding.",
                "Prefer direct-play-friendly formats when possible.",
            ],
            "next_checks": [
                "Check whether transcode count is rising.",
                "Check whether Plex CPU usage increases with active transcodes.",
            ],
            "escalate_if": [
                "Transcodes increase across multiple sessions.",
                "Plex CPU rises significantly.",
                "Multiple clients trigger heavy transcodes at the same time.",
            ],
            "success_signals": [
                "Transcode count falls.",
                "Affected sessions move to direct play or direct stream.",
                "Plex CPU stabilizes.",
            ],
        },
        "network_throughput_issue": {
            "primary_action": "Compare delivered bandwidth with expected bitrate for affected sessions.",
            "recommended_actions": [
                "Compare delivered bandwidth with expected bitrate for affected sessions.",
                "Test an alternate network path or client for the affected session.",
            ],
            "next_checks": [
                "Check whether delivery_below_expected persists across repeated checks.",
                "Compare affected sessions to healthy sessions at the same time.",
            ],
            "escalate_if": [
                "Delivery mismatch persists across multiple checks.",
                "Additional sessions begin showing the same throughput mismatch.",
            ],
            "success_signals": [
                "Delivered bandwidth rises closer to expected bitrate.",
                "Playback stabilizes without server-side load increases.",
            ],
        },
        "client_or_network": {
            "primary_action": "Test another client on the same content.",
            "recommended_actions": [
                "Test another client on the same content.",
                "Check the affected client network path and local conditions.",
            ],
            "next_checks": [
                "Check whether the same client shows repeated failures.",
                "Compare the affected session against healthy sessions and delivery data.",
            ],
            "escalate_if": [
                "The issue spreads to more sessions.",
                "The same client repeatedly degrades across titles.",
            ],
            "success_signals": [
                "The issue remains isolated and clears on retest.",
                "Alternative clients continue playing normally.",
            ],
        },
    }

    plan = dict(default_plan)
    plan.update(plans.get(diagnosis, {}))

    if issue_metadata.get("severity") == "critical":
        plan["recommended_actions"] = plan["recommended_actions"] + [
            "Treat this as an active operator issue until service impact stabilizes."
        ]

    if facts.get("same_content_playing_elsewhere_successfully") and diagnosis == "client_network_path_sensitivity":
        plan["next_checks"] = [
            "Confirm that the same media continues playing successfully on another client.",
            *plan["next_checks"],
        ]

    plan["recommended_actions"] = plan["recommended_actions"][:4]
    plan["next_checks"] = plan["next_checks"][:3]
    plan["escalate_if"] = plan["escalate_if"][:3]
    plan["success_signals"] = plan["success_signals"][:3]

    return plan
