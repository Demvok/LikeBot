from pandas import DataFrame, Series, concat
from datetime import time, datetime
from numpy import random, arange, zeros_like
from scipy.stats import skewnorm

profiles = [
    {
        'name': "Default",
        'probable_points': [  # time, strength (0-1), dispersion (in seconds), skew (-1 - 1)
            (time(hour=7, minute=0), 0.6, 10, 0),
            (time(hour=13, minute=15), 0.8, 15, 1),
            (time(hour=21, minute=30), 1.0, 30, -1),
        ],
        'awakening': time(hour=7, minute=0),
        'sleeping': time(hour=22, minute=0)
    },
    {
        'name': "Night Owl",
        'probable_points': [
            (time(hour=13, minute=0), 0.6, 10, 0),
            (time(hour=18, minute=15), 0.8, 15, 1),
            (time(hour=2, minute=30), 1.0, 30, -1),
        ],
        'awakening': time(hour=12, minute=0),
        'sleeping': time(hour=6, minute=0)
    }
]

time_limit_for_one_post = 28  # in seconds
min_session_duration = 10  # in minutes
max_session_duration = 60  # in minutes
min_periods_cnt = 1
max_periods_cnt = 5

def get_general_activity_period(profile_id=None):
    # TO BE DELETED
    if profile_id is not None:
        profile = profiles[profile_id]
    else:
        profile = random.choice(profiles, size=1)[0]
    return {'awakening': profile['awakening'], 'sleeping': profile['sleeping']}


def get_profile_activity_periods(profile_id=None, n_periods=None):
    # TO BE DELETED: currently it is very hard and not neccessary to implement connection handling for each client in each task

    if profile_id is not None:
        profile = profiles[profile_id]
    else:
        profile = random.choice(profiles, size=1)[0]
    
    probable_points = DataFrame(profile['probable_points'], columns=['time', 'strength', 'dispersion', 'skew'])

    n_periods = n_periods if n_periods is not None else random.randint(min_periods_cnt, max_periods_cnt + 1)  # Random number of periods if not specified

    # Init
    # That will generate time period of activity during the day
    time_minutes = []
    start_minutes = profile['awakening'].hour * 60 + profile['awakening'].minute  + random.randint(-15, 16)
    end_minutes = profile['sleeping'].hour * 60 + profile['sleeping'].minute  + random.randint(-15, 16)

    for minute in range(start_minutes, end_minutes + 1):
        hours = minute // 60
        mins = minute % 60
        time_minutes.append(time(hour=hours, minute=mins))

    active_time = concat([Series(time_minutes), Series([0]*len(time_minutes))], axis=1).rename(columns={0: "time", 1: "activeness"})


    # Generate activity distribution

    def time_to_minutes(t):
        return t.hour * 60 + t.minute

    minutes = arange(start_minutes, end_minutes + 1)
    activeness = zeros_like(minutes, dtype=float)

    for index, row in probable_points.iterrows():
        t = row['time']
        strength = row['strength']
        dispersion = row['dispersion']
        skew = row['skew']

        center = time_to_minutes(t)
        bell = skewnorm.pdf(minutes, a=skew, loc=center, scale=dispersion)
        bell = bell / bell.max() * strength  # scale to strength
        activeness += bell

    active_time['activeness'] = activeness

    probs = active_time['activeness'].values
    probs = probs / probs.sum()


    # Sample start times (indices) according to activeness
    sampled_indices = random.choice(len(active_time), size=n_periods, p=probs)

    # Generate random period lengths (e.g., 10-60 min)
    period_lengths = random.randint(min_session_duration, max_session_duration + 1, size=n_periods)

    # Get the actual time objects for the start of each period
    start_times = active_time.iloc[sampled_indices]['time'].values

    # Build list of (start, end) tuples
    periods = []
    for start, length in zip(start_times, period_lengths):
        # Convert start to minutes, add length, convert back to time
        start_min = start.hour * 60 + start.minute
        end_min = min(start_min + length, time_to_minutes(profile['sleeping']))  # Don't go past sleeping
        end_time = start.replace(hour=end_min // 60, minute=end_min % 60)
        periods.append((start, end_time))


    post_limits = []

    for start, end in periods:
        duration = (datetime.combine(datetime.today(), end) - datetime.combine(datetime.today(), start)).total_seconds()
        post_limit = round(duration / time_limit_for_one_post)
        # print(f"Period from {start} to {end} - {duration / 60} minutes - {post_limit} posts")
        post_limits.append({'start': start, 'end': end, 'duration': duration, 'post_limit': post_limit})
    
    return post_limits

def estimate_reading_time(text, wpm=None):
    """
    Estimate the reading time for a given text in seconds.
    """
    words = len(text.split())
    if wpm is None:
        wpm_list = arange(160, 301, dtype=int)
        wpm_distribution = skewnorm.pdf(wpm_list, loc=230, scale=30, a=0)
        wpm_distribution = wpm_distribution / wpm_distribution.max()
        probs = wpm_distribution / wpm_distribution.sum()
        wpm = random.choice(wpm_list, p=probs, size=1)[0]
    return round(float(words / wpm * 60), 3)