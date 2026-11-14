
# Kefiya

FinTS Connector for ERPNext (Germany)

This app allows to import bank information from german banks into ERPNext.\
The app currently allows to import "receive" and "pay" payment entries.
The goal is to provide a stable and revision prove solution to sync (German) bank information with ERPNext.\
Best-case scenario: the functionality gets integrated into ERPNext as regional feature.

## Requirements
Python 3.4 <\
[Python FinTS](https://github.com/raphaelm/python-fints) (will be installed)

## Installation
```bash
    cd frappe/bench/folder
    bench get-app kefiya https://github.com/phamos-eu/kefiya
    bench --site [sitename] install-app kefiya
    bench --site [sitename] enable-scheduler   
```
## Features
-  [x] FinTS Login
-  [x] Basic FinTS Import
-  [x] Save data as JSON and attach it
-  [x] Add import scheduler
-  [x] Support 'Pay' payment entry type
-  [x] Bank Account Wizard
-  [x] Interactive progress display
-  [x] Complete improving code style (PEP8 / ESLint)
-  [x] Payment / Sale auto assignment (unattended / wizard)
-  [x] Bank logo for each FinTS login, looked up from the bank's website
-  [x] Bank statement file import (CSV), including DKB Girokonto, DKB VISA card and PayPal exports

## Bank statement files

A PayPal activity export (German CSV) becomes one bank transaction per row that changes the balance. A PayPal fee becomes a transaction of its own, so a payment matches its invoice in full. Of the buyer, only the name is taken over; e-mail address, delivery address and phone number stay in the file. Upload the file as a private file.

If you want to upload your bank's statement file manually and its format is not supported yet, please reach out to us via our contact form at [phamos.eu/contact](https://phamos.eu/contact).

## ToDo

-  [ ] Unit tests / Cypress-tests
-  [ ] Improve import logs
-  [ ] Legal way to publish/get FinTS URL's
[https://www.hbci-zka.de/register/bedingungen_bankenliste.htm](https://www.hbci-zka.de/register/bedingungen_bankenliste.htm)
-  [ ] ...

### License

MIT License