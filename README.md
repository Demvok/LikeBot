## Tasks

### TO DO:

- implement various exception messages for client errors

- implement task report logic (task, date, status, errors from accounts (async exception handling))
- add api for task import

- automate an provide manual way of session verification
- route account to session creation for first use
- add account status as meta

- add creation of neccessary files if they dont exist
- add check for validated posts if they still exist
- implement time tracking into logs or abandon it
- implement proxy logic (import, status, links to accounts)

- check for id type consistency
- 

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