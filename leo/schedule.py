"""Horario docente (LEO_SPEC F3.3): cursos semanales, periodo académico, festivos y avisos de clase.

Importación local desde PDF (`pdftotext -layout`, argv fijo) o ICS. Importar nunca guarda: devuelve
candidatos que el usuario confirma en el panel. Los avisos salen N minutos antes por `Events`
(pose, globo y notificación; la extensión respeta concentración y pantalla completa).
"""
from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
import time

from .config import data_dir
from .events import plain

DAYS = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]
DAY_NAMES = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
ICS_DAYS = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}
TIME = re.compile(r"\b([01]?\d|2[0-3])[:.h]([0-5]\d)\b")
RANGE = re.compile(r"\b([01]?\d|2[0-3])[:.h]([0-5]\d)\s*(?:-|–|a|hasta)\s*([01]?\d|2[0-3])[:.h]([0-5]\d)\b")
ROOM = re.compile(r"\b((?:aula|sal[oó]n|lab(?:oratorio)?|sala)\s*[\w.-]+)", re.I)
IMPORT_LIMIT = 5 * 1024 * 1024


def _norm(text):
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFKD", text.casefold()) if not unicodedata.combining(c))


def _hhmm(text):
    match = re.fullmatch(r"([01]?\d|2[0-3]):([0-5]\d)", text or "")
    if not match:
        raise ValueError("Hora HH:MM inválida")
    return f"{int(match[1]):02d}:{match[2]}"


def _day(text):
    try:
        return date.fromisoformat(text)
    except (TypeError, ValueError):
        raise ValueError("Fecha AAAA-MM-DD inválida") from None


def clean_course(course, folders=()):
    if not isinstance(course, dict):
        raise ValueError("Curso inválido")
    day = course.get("day")
    if type(day) is not int or not 0 <= day <= 6:
        raise ValueError("Día de la semana inválido")
    start, end = _hhmm(course.get("start")), _hhmm(course.get("end"))
    if end <= start:
        raise ValueError("La clase debe terminar después de empezar")
    folder = course.get("folder") or ""
    if folder and folder not in {f["id"] for f in folders}:
        raise ValueError("Carpeta de curso desconocida")
    return {"name": plain(course.get("name"), 80, "nombre del curso"), "day": day, "start": start, "end": end,
            "room": plain(course["room"], 40, "salón") if course.get("room") else "", "folder": folder}


def parse_ics(text):
    """Weekly classes from an ICS export: RRULE weekly events, or repeated single events."""
    lines = []
    for raw in text.replace("\r\n", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    events, current = [], None
    for line in lines:
        if line == "BEGIN:VEVENT":
            current = {}
        elif line == "END:VEVENT" and current is not None:
            events.append(current)
            current = None
        elif current is not None and ":" in line:
            key, value = line.split(":", 1)
            current[key.split(";")[0].upper()] = value.strip()

    def moment(value):
        utc = value.endswith("Z")
        parsed = datetime.strptime(value.rstrip("Z")[:15], "%Y%m%dT%H%M%S")
        return parsed.replace(tzinfo=timezone.utc).astimezone().replace(tzinfo=None) if utc else parsed

    def unescape(value):
        return value.replace("\\n", " ").replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\")
    found, single, first, last = {}, {}, None, None
    for event in events:
        try:
            start, end = moment(event["DTSTART"]), moment(event["DTEND"])
        except (KeyError, ValueError):
            continue
        name, room = unescape(event.get("SUMMARY", "")).strip(), unescape(event.get("LOCATION", "")).strip()
        if not name:
            continue
        first = min(first or start.date(), start.date())
        rule = dict(part.split("=", 1) for part in event.get("RRULE", "").split(";") if "=" in part)
        if rule.get("FREQ") == "WEEKLY":
            days = [ICS_DAYS[d[-2:]] for d in rule.get("BYDAY", "").split(",") if d[-2:] in ICS_DAYS] or [start.weekday()]
            if "UNTIL" in rule:
                until = datetime.strptime(rule["UNTIL"][:8], "%Y%m%d").date()
                last = max(last or until, until)
            for day in days:
                found[(name, day, f"{start:%H:%M}")] = {"name": name, "day": day, "start": f"{start:%H:%M}",
                                                        "end": f"{end:%H:%M}", "room": room}
        else:
            key = (name, start.weekday(), f"{start:%H:%M}")
            single.setdefault(key, []).append({"name": name, "day": start.weekday(), "start": f"{start:%H:%M}",
                                               "end": f"{end:%H:%M}", "room": room})
            last = max(last or start.date(), start.date())
    for key, items in single.items():
        if len(items) >= 2 and key not in found:
            found[key] = items[0]
    courses = sorted(found.values(), key=lambda c: (c["day"], c["start"], c["name"]))
    term = {"start": first.isoformat(), "end": last.isoformat()} if first and last else None
    return {"courses": courses, "term": term}


def parse_timetable_text(text):
    """Heuristic reading of `pdftotext -layout` output: day-by-day lists or a grid with days as columns."""
    lines = [line.rstrip() for line in text.splitlines()]
    courses = []
    # Grid: a header naming several days; each row starts with a time range; cells sit under the days.
    for index, line in enumerate(lines):
        normalized = _norm(line)
        columns = [(m.start(), DAYS.index(m[1])) for m in re.finditer(r"\b(" + "|".join(DAYS) + r")\b", normalized)]
        if len(columns) < 3:
            continue
        bounds = [(pos, day, columns[i + 1][0] if i + 1 < len(columns) else 10 ** 6) for i, (pos, day) in enumerate(columns)]
        row_courses = {}
        for row in lines[index + 1:]:
            if not row.strip():
                row_courses = {}  # A blank line ends the cells above; footers never join a course.
                continue
            match = RANGE.search(row)
            if match and match.start() > columns[0][0]:
                match = None
            if not match and not row_courses:
                continue
            if match:
                row_courses = {}
            for pos, day, stop in bounds:
                left = max(match.end() if match else 0, pos - 4 if pos > 4 else 0)
                cell = row[left:stop].strip()
                if len(cell) < 3:
                    continue
                room = ROOM.search(cell)
                name = (cell[:room.start()] if room else cell).strip(" -·,")
                if not match:
                    # Continuation line of the cells above: their room or the rest of the name.
                    course = row_courses.get(day)
                    if course and room and not course["room"]:
                        course["room"] = room[1]
                    elif course and name and len(course["name"]) + len(name) < 80:
                        course["name"] += " " + name
                elif len(name) >= 3:
                    row_courses[day] = {"name": name, "day": day, "start": f"{int(match[1]):02d}:{match[2]}",
                                        "end": f"{int(match[3]):02d}:{match[4]}", "room": room[1] if room else ""}
                    courses.append(row_courses[day])
        if courses:
            break
    if not courses:
        # List: «Lunes 08:00-10:00 Redes Neuronales Aula 3.2», or a day heading followed by time lines.
        day = None
        for line in lines:
            normalized = _norm(line)
            heading = re.match(r"\s*(" + "|".join(DAYS) + r")\b", normalized)
            if heading:
                day = DAYS.index(heading[1])
            match = RANGE.search(line)
            if day is None or not match:
                continue
            rest = (line[:match.start()] + " " + line[match.end():])
            rest = re.sub(r"(?i)^\s*(" + "|".join(DAY_NAMES + DAYS) + r")\b", "", rest.strip()).strip(" -·,:|")
            room = ROOM.search(rest)
            name = re.sub(r"\(\s*\)", "", rest[:room.start()] + rest[room.end():] if room else rest).strip(" -·,:|")
            if len(name) >= 3:
                courses.append({"name": re.sub(r"\s{2,}", " ", name), "day": day,
                                "start": f"{int(match[1]):02d}:{match[2]}", "end": f"{int(match[3]):02d}:{match[4]}",
                                "room": room[1] if room else ""})
    unique = {(c["name"], c["day"], c["start"]): c for c in courses}
    return {"courses": sorted(unique.values(), key=lambda c: (c["day"], c["start"], c["name"])), "term": None}


def import_file(path, run=subprocess.run):
    """Candidates from a file the user chose in the panel; nothing is saved here."""
    path = Path(path)
    if not path.is_file() or path.stat().st_size > IMPORT_LIMIT:
        raise ValueError("Elige un archivo PDF o ICS de hasta 5 MB")
    if path.suffix.lower() == ".ics":
        return parse_ics(path.read_text(errors="replace"))
    if path.suffix.lower() != ".pdf":
        raise ValueError("Formatos admitidos: .pdf y .ics")
    executable = shutil.which("pdftotext", path="/usr/bin:/bin")
    if not executable:
        raise RuntimeError("Falta pdftotext (paquete poppler-utils)")
    result = run([executable, "-layout", "-enc", "UTF-8", str(path), "-"], capture_output=True, text=True, timeout=30,
                 env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"})
    if result.returncode:
        raise RuntimeError("pdftotext no pudo leer el PDF")
    return parse_timetable_text(result.stdout)


class Schedule:
    def __init__(self, events, settings, path=None, clock=time.time, interval=30.0):
        self.events, self.settings, self.clock, self.interval = events, settings, clock, interval
        self.path = path or data_dir() / "schedule.json"
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.thread = None
        try:
            self.data = json.loads(self.path.read_text())
            assert isinstance(self.data, dict)
        except (OSError, ValueError, AssertionError):
            self.data = {}
        self.data = {"courses": self.data.get("courses", []), "term": self.data.get("term"),
                     "holidays": self.data.get("holidays", []), "alerted": self.data.get("alerted", [])}

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, name = tempfile.mkstemp(prefix=".schedule-", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(self.data, stream, ensure_ascii=False)
            os.replace(name, self.path)
        finally:
            Path(name).unlink(missing_ok=True)

    def get(self):
        with self.lock:
            return {k: v for k, v in json.loads(json.dumps(self.data)).items() if k != "alerted"}

    def set(self, courses, term=None, holidays=()):
        if not isinstance(courses, list) or len(courses) > 80:
            raise ValueError("Horario: hasta 80 clases semanales")
        folders = self.settings.snapshot()["folders"]
        cleaned = [{"id": i + 1, **clean_course(c, folders)} for i, c in enumerate(courses)]
        if term is not None:
            if not isinstance(term, dict) or set(term) != {"start", "end"} or _day(term["start"]) > _day(term["end"]):
                raise ValueError("Periodo académico inválido")
        if not isinstance(holidays, (list, tuple)) or len(holidays) > 100:
            raise ValueError("Festivos: lista de fechas")
        holidays = sorted({_day(d).isoformat() for d in holidays})
        with self.lock:
            self.data.update(courses=cleaned, term=term, holidays=holidays)
            self._save()
        return self.get()

    def classes_on(self, day):
        with self.lock:
            term, holidays, courses = self.data["term"], set(self.data["holidays"]), list(self.data["courses"])
        if day.isoformat() in holidays or (term and not term["start"] <= day.isoformat() <= term["end"]):
            return []
        return sorted((c for c in courses if c["day"] == day.weekday()), key=lambda c: c["start"])

    def in_class(self):
        """True during a class of today's timetable: Leo atento does not observe while the user teaches."""
        now = datetime.fromtimestamp(self.clock())
        moment = now.strftime("%H:%M")
        return any(c["start"] <= moment < c["end"] for c in self.classes_on(now.date()))

    def day(self, when):
        """Classes for «hoy», «mañana» or AAAA-MM-DD, with the course folder path to open its materials."""
        today = datetime.fromtimestamp(self.clock()).date()
        key = _norm(when or "hoy").strip()
        target = today if key in {"hoy", "today"} else today + timedelta(days=1) if key in {"manana", "tomorrow"} else _day(when)
        folders = {f["id"]: f["path"] for f in self.settings.snapshot()["folders"]}
        classes = [{"curso": c["name"], "inicio": c["start"], "fin": c["end"], "salon": c["room"],
                    "carpeta": folders.get(c["folder"], "")} for c in self.classes_on(target)]
        reason = ""
        with self.lock:
            if target.isoformat() in self.data["holidays"]:
                reason = "festivo"
            elif self.data["term"] and not self.data["term"]["start"] <= target.isoformat() <= self.data["term"]["end"]:
                reason = "fuera del periodo académico"
        return {"fecha": target.isoformat(), "dia": DAY_NAMES[target.weekday()], "clases": classes,
                **({"nota": reason} if reason else {}), **({} if self.data["courses"] else {"nota": "no hay horario importado"})}

    def check(self):
        minutes = self.settings.snapshot()["class_alert_minutes"]
        if not minutes:
            return 0
        now = datetime.fromtimestamp(self.clock())
        sent = 0
        for course in self.classes_on(now.date()):
            start = datetime.combine(now.date(), datetime.strptime(course["start"], "%H:%M").time())
            key = f"{now.date().isoformat()}:{course['id']}"
            if not start - timedelta(minutes=minutes) <= now < start:
                continue
            with self.lock:
                if key in self.data["alerted"]:
                    continue
                self.data["alerted"] = (self.data["alerted"] + [key])[-200:]
                self._save()
            left = max(1, round((start - now).total_seconds() / 60))
            room = f" en el salón {course['room']}" if course["room"] else ""
            title = f"En {left} min tienes {course['name']}{room}."
            title += " ¿Abro los materiales?" if course["folder"] else ""
            try:
                self.events.publish(f"class:{course['id']}", "attention", title[:120], "", "schedule")
                sent += 1
            except (RuntimeError, ValueError):
                pass
        return sent

    def start(self):
        self.thread = threading.Thread(target=self._loop, name="leo-schedule", daemon=True)
        self.thread.start()

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=5)

    def _loop(self):
        while not self.stop.wait(self.interval):
            try:
                self.check()
            except Exception:
                pass

    def dispatch(self, action, args):
        """Explicit panel actions; importing returns candidates for the confirmation screen."""
        if not isinstance(args, dict):
            raise ValueError("Argumentos inválidos")
        if action == "get" and not args:
            return self.get()
        if action == "import" and set(args) == {"path"} and isinstance(args["path"], str):
            return import_file(args["path"])
        if action == "set" and {"courses"} <= set(args) <= {"courses", "term", "holidays"}:
            return self.set(args["courses"], args.get("term"), args.get("holidays", []))
        raise ValueError("Acción de horario inválida")
