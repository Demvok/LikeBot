## Tasks

### TO DO:

- add api for task import

- add account status as meta (login status)
- automate an provide manual way of session verification
- route account to session creation for first use

- add creation of neccessary files if they dont exist
- implement proxy logic (import, status, links to accounts)

- ENSURE DATA TYPES CONSISTENCY
- add signal stops for reporter context

- add object refresh on client for first account_id retrieval or fix logging to use phone number
- add reaction precondition (exists in chat) check
- multiple post import


## Changelog

### v.0.4



### v0.3
- implemented task report logic (task, date, status, errors from accounts (async exception handling))
- implemented crash report logic
- updated task and runs status during run
- rewritten connect clients to async
- implemented various exception messages for client errors
- post ids in task now be sorted

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