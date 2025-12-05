## Tasks

### TO DO:

- redo analysis & test

##### Account health system - stage 2
- Add chat "opening" before reacting? & add "warming-up" and "cooling" stage for account connect, that should help

##### Account health system - stage 3
- add special logging of account's action with markers
- Add health monitor system that catches ban factors and applies limits
- provide account managing tools - freezing, status check from SpamBot

##### Account health system - stage 4
- rework task assigning for each account - try to make it more "individualistic"
- change general task-account linkage - it should be dynamic

##### Misc
- add creation of neccessary files if they dont exist
- add report export as .csv/.xlsx
- add .xlsx proxy import
- refactor `task.py` and split it into blocks
- add counter table reset on deletion
- fix counters logic - merges new objects with old ones
- fix proxy tester - fails to parse 2ip.ua output

#### TO TEST:
- added task cancelling on API stop (WIP)
- included chat_id to chat username mapping into channel parser

## Changelog

### v.1.1.4
- implemented process-scope caching (with safe fallback to old logic for now)
- fixed unsuccessful cache deduplication
- tied proxies directly to accounts (up to five per account) with new link/unlink endpoints
- replaced runtime load balancing with linked account counters and least-linked proxy reporting
- added configurable proxy auto-assignment helper and API endpoint to balance proxies per account
- added 2ip.ua-based proxy connectivity test endpoint for quick SOCKS/HTTP validation

### v.1.1.3
- removed entity caching on task scope, still is used per client
- fixed incorrect task crashing on account error
- fixed retries on channel name resolution
- fixed post validation racing issues
- fixed channel name racing and retry issues
- added post object reuse between workers to decrease DB calls
- fixed some telegram cache futures

### v.1.1.2
- reviewed and standartized pauses and retries for flood avoiding
- complete caching rework, now works on task scope
- now entity object is cached completely, with thread managing
- reviewed and reduced usage of get_messages() and get_message_content() telegram requests
- saved post content to reduce calls
- fixed channel indexing endpoint
- included chat_id to chat username mapping into channel parser
- reviewed rate limiting and delays
- added task cancelling on API stop (WIP)

### v.1.1.1
- fixed malformed task object creation on low RAM (cleanup and safe loading)
- added endpoint and account meta to index channels
- added endpoint and db method to get all channel's subscribers
- added bulk get for channels (to view details of account's subscriptions)
- added account lock on connect attempt when it is already used (may be bugged)
- added task status `failed` for cases when task failed to finish due to account issues.

### v.1.1.0
- massive file refactor for better readability (without actual code changes)
- added channel tracking and viewing
- added helper functions for channel, post and account relation viewing
- added precondition check for client's action (WIP)
- added proxy crud endpoints and proxy status (active/disabled for now)
- added user register endpoints
- added full channel logic endpoints

### v.1.0.6
- fixed multiple palette import, only on task startup
- added task's runs and events clearup on task delete
- added timestamp storage for task (for more persistence)
- optimized database interactions
- improved task status change on crashes 
- improved and tested proxies

### v.1.0.5
- improved report context handling on crashes   
- centralized telethon error handling
- fixed account error writing (status, last error)
- fixed post validation (to use only active accounts)
- fixed post url parser (yet again)
- ensured views on posts correct counting

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