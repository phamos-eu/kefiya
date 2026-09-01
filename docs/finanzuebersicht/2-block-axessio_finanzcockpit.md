# Custom HTML Block `axessio_finanzcockpit`

Ein Patch zum Einsetzen im Browser, kein lauffähiges Modul — die
Abschnitte sind Bruchstücke aus der Mitte einer Funktion. Deshalb
Markdown: eine Datei mit der Endung `.py` wird beim Release-Check
von Frappe Cloud kompiliert und ein eingerückter Ausschnitt ist
dort ein Syntaxfehler.

```javascript
// ============================================================================
// PATCH 4 von 7  —  Custom HTML Block  axessio_finanzcockpit, Feld "script"
// ----------------------------------------------------------------------------
// SUCHEN (in der Funktion, die mit "Ov(){" beginnt, direkt nach ensureWin()):
//
//   var giro=OV.filter(function(a){return !a.is_loan;});
//   var loan=OV.filter(function(a){return a.is_loan;});
//
// ERSETZEN durch:
// ============================================================================

  /* Der Abschnitt kommt vom Server, weil dort die Kontoart der Bank steht
     (Kefiya Login.account_kind). is_loan ist nur die Rueckfallebene fuer eine
     Antwort, die noch von der Fassung ohne 'bucket' stammt -- solange die im
     Cache liegt, sieht die Seite sonst gar keine Konten mehr. */
  function buck(a){return a.bucket||(a.is_loan?'loan':'pay');}
  var giro=OV.filter(function(a){return buck(a)==='pay';});
  var loan=OV.filter(function(a){return buck(a)==='loan';});
  var aval=OV.filter(function(a){return buck(a)==='aval';});
  var shar=OV.filter(function(a){return buck(a)==='share';});


// ============================================================================
// PATCH 5 von 7  —  gleicher Block, weiter unten im selben Aufbau
// ----------------------------------------------------------------------------
// SUCHEN:
//
//   var giroF=ovFilter(giro),loanF=ovFilter(loan);
//
// ERSETZEN durch:
// ============================================================================

  var giroF=ovFilter(giro),loanF=ovFilter(loan),
      avalF=ovFilter(aval),sharF=ovFilter(shar);
  /* Ein Abschnitt der Uebersicht. Leer bleibt er weg -- ausser der Filter hat
     ihn geleert, dann sagt er das. Ein Abschnitt, der wegen eines Filters
     verschwindet, sieht sonst aus wie fehlende Daten. */
  function ovSec(titel,gefiltert,alle,ohneVorschau){
    if(gefiltert.length)
      return `<div class='fcc-h2'>${titel}</div>`+tbl(gefiltert,ohneVorschau);
    if(alle.length&&(OVF.kpi||OVF.q))
      return `<div class='fcc-h2'>${titel}</div>`
        +`<div class='fcc-sub'>Kein Konto in diesem Abschnitt passt zum Filter.</div>`;
    return '';
  }

// ----------------------------------------------------------------------------
// UND, wenige Zeilen darunter. ACHTUNG: "Girokonten" steht ZWEIMAL im Block.
// Gemeint ist NUR die Zeile mit fcc-ovf; die Ueberschrift
// "Liquiditaetsentwicklung (Summe aller Girokonten)" bleibt, wie sie ist --
// sie stimmt jetzt sogar erst, weil giro nur noch Zahlungskonten enthaelt.
//
// SUCHEN:
//   h+=`<div class='fcc-warn fcc-ovf'>Gefiltert: ${teile.join(' · ')} — ${giroF.length} von ${giro.length} Girokonten${loan.length?(', '+loanF.length+' von '+loan.length+' Darlehen'):''} <button id='fcc-ovclear' class='fcc-mini'>Filter aufheben</button></div>`;
//
// ERSETZEN durch:
// ----------------------------------------------------------------------------

    h+=`<div class='fcc-warn fcc-ovf'>Gefiltert: ${teile.join(' · ')} — ${giroF.length} von ${giro.length} Zahlungskonten${(loan.length+aval.length+shar.length)?(', '+(loanF.length+avalF.length+sharF.length)+' von '+(loan.length+aval.length+shar.length)+' weiteren Konten'):''} <button id='fcc-ovclear' class='fcc-mini'>Filter aufheben</button></div>`;

// ----------------------------------------------------------------------------
// UND, unmittelbar danach:
//
// SUCHEN:
//   h+=ovTxBlock();
//   h+=tbl(giroF,false);
//   if(loanF.length){h+=`<div class='fcc-h2'>Darlehenskonten</div>`+tbl(loanF,true);}
//   else if(loan.length&&(OVF.kpi||OVF.q)){h+=`<div class='fcc-h2'>Darlehenskonten</div><div class='fcc-sub'>Kein Darlehenskonto passt zum Filter.</div>`;}
//
// ERSETZEN durch:
// ----------------------------------------------------------------------------

  h+=ovTxBlock();
  h+=tbl(giroF,false);
  /* Vier Abschnitte, in der Reihenfolge, in der man sie liest: womit gezahlt
     wird, was geschuldet wird, was zugesagt ist, was gebunden ist. Depots
     fehlen mit Absicht -- die stehen im WAL-Management, und der Server nimmt
     sie deshalb schon aus der Antwort heraus.
     Der zweite Parameter von tbl() blendet die Vorschauspalte aus: eine
     Zahlungsvorschau hat nur, wovon auch gezahlt wird. */
  h+=ovSec('Darlehen',loanF,loan,true);
  h+=ovSec('Avale und Kreditlinien',avalF,aval,true);
  h+=ovSec('Geschäftsanteile',sharF,shar,true);


// ============================================================================
// PATCH 6 von 7  —  gleicher Block: die Abschnitte duerfen sich nicht
//                   gegenseitig zuklappen
// ----------------------------------------------------------------------------
// Der Zuklapp-Zustand je Unternehmen wird aus einem Schluessel gebildet, der
// bisher nur zwei Werte kannte: Giro oder Darlehen. Drei Abschnitte, die alle
// "Darlehen" sind, teilten sich damit einen Schluessel -- "Volksbank"
// zuklappen haette es unter Darlehen, Avalen UND Geschaeftsanteilen zugeklappt.
//
// SUCHEN:
//   function coKey(isLoan,co){return (isLoan?'L:':'G:')+co;}
//
// ERSETZEN durch:
// ----------------------------------------------------------------------------

function coKey(sec,co){return (sec===true?'L:':(sec===false?'G:':(sec+':')))+co;}

// ----------------------------------------------------------------------------
// UND in tbl(): den Abschnitt durchreichen und die Vorschausumme daran binden.
//
// SUCHEN:
//   function tbl(list,isLoan){
//
// ERSETZEN durch:
// ----------------------------------------------------------------------------

function tbl(list,isLoan,sec){

// ----------------------------------------------------------------------------
// SUCHEN (in derselben Funktion):
//   g[co].forEach(function(a){sb+=(a.balance||0);sa+=(a.available||0);if(!a.is_loan)sfc+=(a.forecast||0);});
//
// ERSETZEN durch:
// ----------------------------------------------------------------------------

    /* An der Spalte, nicht am Konto: wo keine Vorschauspalte steht, gehoert
       auch keine Vorschausumme hin. Vorher hing das an is_loan, und ein Aval
       ist kein Darlehen -- seine Zahl waere unter einer leeren Spalte
       aufsummiert worden. */
    g[co].forEach(function(a){sb+=(a.balance||0);sa+=(a.available||0);if(!isLoan)sfc+=(a.forecast||0);});

// ----------------------------------------------------------------------------
// SUCHEN (in derselben Funktion, wenige Zeilen darunter):
//   var key=coKey(isLoan,co);
//
// ERSETZEN durch:
// ----------------------------------------------------------------------------

    var key=coKey(sec===undefined?isLoan:sec,co);


// ============================================================================
// PATCH 7 von 7  —  die vier Aufrufe bekommen ihren Abschnittsnamen
// ----------------------------------------------------------------------------
// Das ersetzt die drei ovSec-Zeilen aus Patch 5 (und die tbl-Zeile davor).
// Wenn Sie Patch 5 schon eingesetzt haben, tauschen Sie nur diese vier Zeilen
// gegen die folgenden aus:
// ----------------------------------------------------------------------------

  h+=tbl(giroF,false,'pay');
  h+=ovSec('Darlehen',loanF,loan,true,'loan');
  h+=ovSec('Avale und Kreditlinien',avalF,aval,true,'aval');
  h+=ovSec('Geschäftsanteile',sharF,shar,true,'share');

// ----------------------------------------------------------------------------
// UND ovSec aus Patch 5 nimmt den Abschnittsnamen entgegen:
// ----------------------------------------------------------------------------

  function ovSec(titel,gefiltert,alle,ohneVorschau,sec){
    if(gefiltert.length)
      return `<div class='fcc-h2'>${titel}</div>`+tbl(gefiltert,ohneVorschau,sec);
    if(alle.length&&(OVF.kpi||OVF.q))
      return `<div class='fcc-h2'>${titel}</div>`
        +`<div class='fcc-sub'>Kein Konto in diesem Abschnitt passt zum Filter.</div>`;
    return '';
  }
```
