## Tasks

### TO DO:

Must:

- write task creation logic (start/pause/stop, status, api, csv import)
- implement human-like delays (config)
- add connection retries (config)
- add action retries (config)
- add account status as meta
- configure ALL filepaths in config

Should:
- automate an provide manual way of session verification
- route account to session creation for first use
- implement various exception messages for client errors
- implement task report logic (task, date, status, errors from accounts (async exception handling))

Could:
- create emoji palettes
- implement proxy logic (import, status, links to accounts)


### Questions:
- what to use as account identifier -  session/phone number/account name/create id?


## Changelog

### 07.08.2025
- post import logic
- task and post classes implementation
- post ids verification