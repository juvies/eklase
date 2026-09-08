# const.py
DOMAIN = "eklase_family"

CONF_USERNAME = "username"
CONF_PASSWORD = "password"

CONF_LESSON_TIMES = "lesson_times"
CONF_PROFILE_LESSON_TIMES = "profile_lesson_times"
CONF_REFRESH_INTERVAL = "refresh_interval"

# how often to refresh data from E-klase (default: 60 min)
DEFAULT_UPDATE_INTERVAL_MINUTES = 60

# dafault lesson times (1-10), can be overridden in options
DEFAULT_LESSON_TIMES = [
    {"start": "08:10", "end": "08:50"},  # 1
    {"start": "09:00", "end": "09:40"},  # 2
    {"start": "09:50", "end": "10:30"},  # 3
    {"start": "10:40", "end": "11:20"},  # 4
    {"start": "11:30", "end": "12:10"},  # 5
    {"start": "12:40", "end": "13:20"},  # 6
    {"start": "13:30", "end": "14:10"},  # 7
    {"start": "14:20", "end": "15:00"},  # 8
    {"start": "15:10", "end": "15:50"},  # 9
    {"start": "16:00", "end": "16:40"},  # 10
]

CONF_WATCH_DAYS = "watch_days"
DEFAULT_WATCH_DAYS = 2  # look at today + next day; can be overridden in options