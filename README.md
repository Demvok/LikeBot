## Tasks

### TO DO:

- implement various exception messages for client errors
- implement task report logic (task, date, status, errors from accounts (async exception handling))
- add account status as meta (login status)

- add api for task import

- automate an provide manual way of session verification
- route account to session creation for first use

- add creation of neccessary files if they dont exist
- implement proxy logic (import, status, links to accounts)

- ? move sessions into MongoDB
- add passwords into account records + encrypt data
- update task status during run
- post ids in task should be sorted!
- ENSURE DATA TYPES CONSISTENCY
- rewrite connect clients to async

### Questions
- Should I implement 2nd layer of humanisation or drop it?



## Changelog


### v0.2
- configured ALL filepaths in config
- implemented human-like delays (config)
- added connection retries (config)
- added action retries (config)

### v0.1
- post import logic implemented
- task and post classes implementation
- post ids verification
- created emoji palettes
- write task creation logic (start/pause/stop, status, csv import)