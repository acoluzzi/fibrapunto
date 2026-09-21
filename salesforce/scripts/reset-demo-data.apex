// Fibrapunto — dati di prova. Rilanciabile: cancella e ricrea.
delete [SELECT Id FROM Fibrapunto_Contract__c];
delete [SELECT Id FROM Account WHERE Name IN ('Marco Bellini','Giulia Fontana','Sandro Ricci')];

Account a1 = new Account(Name='Marco Bellini');
Account a2 = new Account(Name='Giulia Fontana');
Account a3 = new Account(Name='Sandro Ricci');
insert new List<Account>{a1, a2, a3};

insert new List<Fibrapunto_Contract__c>{
  // caso principale: addebito respinto
  new Fibrapunto_Contract__c(Name='FP-2023-048812', Account__c=a1.Id,
    Plan__c='Fibra 1000', Modem_Serial__c='FPM-8841-2023',
    Activation_Date__c=Date.newInstance(2023,4,10),
    Lock_in_Until__c=Date.newInstance(2025,4,10), Status__c='Active'),

  // caso disdetta: vincolo scaduto
  new Fibrapunto_Contract__c(Name='FP-2021-017350', Account__c=a2.Id,
    Plan__c='Fibra 300', Modem_Serial__c='FPM-2210-2021',
    Activation_Date__c=Date.newInstance(2021,3,1),
    Lock_in_Until__c=Date.newInstance(2023,3,1), Status__c='Active'),

  // vincolo ancora attivo
  new Fibrapunto_Contract__c(Name='FP-2025-102233', Account__c=a3.Id,
    Plan__c='Fibra 1000', Modem_Serial__c='FPM-9903-2025',
    Activation_Date__c=Date.newInstance(2025,6,15),
    Lock_in_Until__c=Date.newInstance(2027,6,15), Status__c='Active'),

  // sospeso
  new Fibrapunto_Contract__c(Name='FP-2024-077104', Account__c=a3.Id,
    Plan__c='FWA', Modem_Serial__c='FPW-5512-2024',
    Activation_Date__c=Date.newInstance(2024,9,1), Status__c='Active'),

  // seconda casa di Marco: serve al caso "due contratti"
  new Fibrapunto_Contract__c(Name='FP-2022-031975', Account__c=a1.Id,
    Plan__c='Fibra 300', Modem_Serial__c='FPM-7730-2022',
    Activation_Date__c=Date.newInstance(2022,11,20),
    Lock_in_Until__c=Date.newInstance(2024,11,20), Status__c='Active'),

  // esisterà in org ma non nel billing: disallineamento CRM/legacy
  new Fibrapunto_Contract__c(Name='FP-2020-004512', Account__c=a2.Id,
    Plan__c='Fibra 300', Modem_Serial__c='FPM-1180-2020',
    Activation_Date__c=Date.newInstance(2020,2,1), Status__c='Active')
};

System.debug('Contratti creati: ' + [SELECT COUNT() FROM Fibrapunto_Contract__c]);