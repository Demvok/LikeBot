## Tasks

### TO DO:

- add task's runs and events clearup on delete
- add neccessary filtering on db loading, not loading all and filtering
- add timestamp for task

- ensure views on posts correct counting

- fix multiple palette import, only once needed

- add creation of neccessary files if they dont exist


## Changelog

### v.1.0.5
- improved report context handling on crashes

### v.1.0.4
- fixed logger file writing issue
- added file size limit for logs
- fixed message url parser
- added more specific error tracking

### v.1.0.3
- secondary database usage moved to `database.py`
- implemented proxy logic with password encryption
- moved reaction palette storage into DB
- added reaction precondition check

### v.1.0.2
- added auth in API endpoints
- managed access to endpoints depending on role

### v.1.0.1
- removed semi-funtional file data storage logic
- ensured data type consistency via pydantic
- routed account to session creation for first use
- added account status as meta (login status)
- added manual way and automated session verification

### v1.0.0 - RELEASE
- full API for basic tasks
- moved to production server
- some API quickfixes for interface calls
- auto-deployment script

### v0.4
- add api for task import
- review data handling on process terminations (shutdowns, cancels, etc.)
- multiple post import

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