"""
notify.py - sends the night summary to your phone as a notification, through ntfy (free app).

Setup: install the ntfy app, subscribe to your topic, and put the topic in .env:

    NTFY_TOPIC=stillwave-something-random

Anyone who knows the topic name can read it, so keep it long and random.
"""

import urllib.error
import urllib.request

import settings

SERVER = "https://ntfy.sh"
MORNING_EMPTY_S = 10 * 60      # out of bed this long ...
MORNING_MIN_IN_BED = 120       # ... after at least this many minutes in bed -> send the morning summary


class NotifyError(Exception):
    pass


def topic():
    return settings.get("NTFY_TOPIC")


def send(title, message, tags="sleeping"):
    t = topic()
    if not t:
        raise NotifyError("No NTFY_TOPIC in .env. Add NTFY_TOPIC=your-topic and restart the server.")
    req = urllib.request.Request(f"{SERVER}/{t}", data=message.encode("utf-8"), method="POST",
                                 headers={"Title": title, "Tags": tags})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp.read()
    except urllib.error.HTTPError as e:
        raise NotifyError(f"ntfy refused the message (HTTP {e.code}).")
    except (urllib.error.URLError, TimeoutError) as e:
        raise NotifyError(f"Can't reach ntfy (no internet?): {e}")


def _hm(m):
    return f"{m // 60}h {m % 60:02d}m" if m >= 60 else f"{m}m"


def night_message(report, summary_text=None):
    """One line of numbers, then the Gemini summary if there is one."""
    parts = [f"In bed {_hm(report['in_bed_min'])}", f"asleep ~{_hm(report['asleep_min'])}"]
    if report.get("breath_avg"):
        parts.append(f"{round(report['breath_avg'])} breaths/min")
    dips = f"{report['dip_count']} breathing dips"
    if report.get("dips_per_hour") is not None:
        dips += f" ({report['dips_per_hour']}/h)"
    parts.append(dips)
    text = " · ".join(parts)
    if summary_text:
        text += "\n\n" + summary_text
    return text


def week_message(week):
    """Screen-Time-style weekly roll-up."""
    n = week["nights"]
    parts = [f"{n} night{'s' if n != 1 else ''} tracked",
             f"avg {_hm(week['avg_in_bed_min'])} in bed",
             f"asleep ~{_hm(week['avg_asleep_min'])}"]
    if week.get("breath_avg"):
        parts.append(f"{round(week['breath_avg'])} breaths/min")
    if week.get("dips_per_hour") is not None:
        parts.append(f"{week['dips_per_hour']} breathing dips/h")
    longest = week["longest"]
    return " · ".join(parts) + f"\n\nLongest night: {longest['night']} ({_hm(longest['in_bed_min'])} in bed)."
