## Tasks

### TO DO:

Must:

- implement task report logic (task, date, status, errors from accounts (async exception handling))
- add api for task import
- add account status as meta

Should:
- automate an provide manual way of session verification
- route account to session creation for first use
- implement various exception messages for client errors
- add creation of neccessary files if they dont exist

Could:
- implement proxy logic (import, status, links to accounts)


### Questions:
- 123


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