"""Default personal chart of accounts (Swiss-style numbering), in French and English."""

from sqlalchemy.orm import Session

from .models import Account, Ledger

# (code, parent_code, type, kind, is_group, fr, en, ai hint)
CHART: list[tuple[str, str | None, str, str, bool, str, str, str]] = [
    # ---- 1 Actifs
    ("1", None, "asset", "other", True, "Actifs", "Assets", ""),
    ("10", "1", "asset", "other", True, "Liquidités", "Cash & bank", ""),
    ("1000", "10", "asset", "cash", False, "Caisse (espèces)", "Cash", "physical cash wallet"),
    ("1010", "10", "asset", "bank", False, "Comptes de paiement en ligne", "Online payment accounts",
     "Revolut, PayPal, Wise and similar"),
    ("1020", "10", "asset", "bank", False, "Compte courant", "Current account", "main bank account"),
    ("1030", "10", "asset", "bank", False, "Compte épargne", "Savings account", ""),
    ("1090", "10", "asset", "suspense", False, "Compte d'attente (à classer)", "Suspense (to classify)",
     "never choose: placeholder for unclassified items"),
    ("11", "1", "asset", "other", True, "Créances", "Receivables", ""),
    ("1100", "11", "asset", "receivable", True, "Avances à des tiers", "Advances to people",
     "money I paid for someone else and that they owe me back; one sub-account per person"),
    ("1170", "11", "asset", "other", False, "Impôt anticipé à récupérer", "Withholding tax refundable", ""),
    ("1180", "11", "asset", "other", False, "Garanties de loyer & cautions", "Rent deposits & guarantees", ""),
    ("12", "1", "asset", "other", True, "Avoirs prépayés", "Prepaid balances", ""),
    ("1200", "12", "asset", "other", False, "Cartes prépayées & avoirs", "Prepaid cards & credits",
     "top-ups of laundry cards, transport cards, gift cards, prepaid balances"),
    ("1300", "1", "asset", "other", False, "Charges payées d'avance", "Prepaid expenses", ""),
    ("14", "1", "asset", "other", True, "Placements", "Investments", ""),
    ("1400", "14", "asset", "other", False, "Titres & investissements", "Securities & investments", ""),
    ("1410", "14", "asset", "other", False, "Prévoyance 3e pilier", "Pillar 3a retirement", "3a contributions"),
    ("15", "1", "asset", "other", True, "Immobilisations", "Fixed assets", ""),
    ("1500", "15", "asset", "other", False, "Véhicules", "Vehicles", ""),
    ("1510", "15", "asset", "other", False, "Mobilier & équipement", "Furniture & equipment", ""),
    # ---- 2 Passifs
    ("2", None, "liability", "other", True, "Passifs", "Liabilities & equity", ""),
    ("20", "2", "liability", "other", True, "Dettes à court terme", "Short-term liabilities", ""),
    ("2000", "20", "liability", "other", False, "Factures à payer", "Bills payable", ""),
    ("2050", "20", "liability", "credit_card", True, "Cartes de crédit", "Credit cards",
     "credit card accounts; a bank payment to the card issuer settles the card balance"),
    ("2051", "2050", "liability", "credit_card", False, "Carte de crédit", "Credit card", ""),
    ("2100", "20", "liability", "receivable", True, "Dettes envers des tiers", "Money owed to people",
     "money someone advanced for me that I must pay back; one sub-account per person"),
    ("2200", "20", "liability", "other", False, "Impôts à payer", "Taxes payable", ""),
    ("2400", "2", "liability", "other", False, "Prêts & hypothèques", "Loans & mortgages", ""),
    ("28", "2", "liability", "equity", True, "Capitaux propres", "Equity", ""),
    ("2800", "28", "liability", "equity", False, "Capital / soldes initiaux", "Opening balances / equity",
     "never choose: used for opening balances"),
    # ---- 3 Produits
    ("3", None, "income", "other", True, "Produits", "Income", ""),
    ("3000", "3", "income", "other", False, "Salaire", "Salary", "employer salary payments"),
    ("3010", "3", "income", "other", False, "Bonus & 13e salaire", "Bonus", ""),
    ("3100", "3", "income", "other", False, "Revenus indépendants", "Freelance income", ""),
    ("3200", "3", "income", "other", False, "Intérêts & dividendes", "Interest & dividends", ""),
    ("3300", "3", "income", "other", False, "Remboursements & prestations", "Refunds & benefits",
     "insurance refunds, health insurance reimbursements, allowances"),
    ("3400", "3", "income", "other", False, "Cadeaux reçus", "Gifts received", ""),
    ("3500", "3", "income", "other", False, "Ventes d'objets", "Sales of items", ""),
    ("3900", "3", "income", "other", False, "Autres produits", "Other income", ""),
    # ---- 4 Charges
    ("4", None, "expense", "other", True, "Charges", "Expenses", ""),
    ("40", "4", "expense", "other", True, "Logement", "Housing", ""),
    ("4000", "40", "expense", "other", False, "Loyer", "Rent", ""),
    ("4010", "40", "expense", "other", False, "Énergie & charges", "Utilities", "electricity, heating, water"),
    ("4020", "40", "expense", "other", False, "Internet & téléphone", "Internet & phone",
     "mobile plan, internet, hosting, domains"),
    ("4030", "40", "expense", "other", False, "Entretien & ménage", "Household & maintenance",
     "cleaning products, laundry, repairs"),
    ("41", "4", "expense", "other", True, "Alimentation", "Food", ""),
    ("4100", "41", "expense", "other", False, "Courses", "Groceries", "Migros, Coop, Denner, Aldi, Lidl"),
    ("4110", "41", "expense", "other", False, "Restaurants & bars", "Restaurants & bars", ""),
    ("4120", "41", "expense", "other", False, "Livraison de repas", "Food delivery", "Uber Eats, Smood, Just Eat"),
    ("42", "4", "expense", "other", True, "Transport", "Transport", ""),
    ("4200", "42", "expense", "other", False, "Transports publics", "Public transport", "SBB CFF, TL, Mobilis"),
    ("4210", "42", "expense", "other", False, "Voiture & carburant", "Car & fuel", ""),
    ("4220", "42", "expense", "other", False, "Taxi & VTC", "Taxi & ride-hailing", "Uber rides (not Uber Eats)"),
    ("4230", "42", "expense", "other", False, "Vélo & micro-mobilité", "Bike & micromobility", ""),
    ("43", "4", "expense", "other", True, "Santé", "Health", ""),
    ("4300", "43", "expense", "other", False, "Assurance maladie", "Health insurance", "LAMal premiums"),
    ("4310", "43", "expense", "other", False, "Frais médicaux & pharmacie", "Medical & pharmacy", ""),
    ("44", "4", "expense", "other", True, "Assurances", "Insurance", ""),
    ("4400", "44", "expense", "other", False, "Assurances RC & ménage", "Liability & household insurance", ""),
    ("4410", "44", "expense", "other", False, "Autres assurances", "Other insurance", ""),
    ("45", "4", "expense", "other", True, "Impôts & taxes", "Taxes & fees", ""),
    ("4500", "45", "expense", "other", False, "Impôts", "Taxes", ""),
    ("4510", "45", "expense", "other", False, "Taxes & redevances", "Fees & licences", "Serafe, administrative fees"),
    ("46", "4", "expense", "other", True, "Loisirs", "Leisure", ""),
    ("4600", "46", "expense", "other", False, "Loisirs & culture", "Leisure & culture", ""),
    ("4610", "46", "expense", "other", False, "Sport", "Sport", ""),
    ("4620", "46", "expense", "other", False, "Voyages & vacances", "Travel & holidays", ""),
    ("4630", "46", "expense", "other", False, "Abonnements numériques", "Digital subscriptions",
     "streaming, software, cloud services"),
    ("47", "4", "expense", "other", True, "Dépenses personnelles", "Personal", ""),
    ("4700", "47", "expense", "other", False, "Vêtements", "Clothing", ""),
    ("4710", "47", "expense", "other", False, "Soins personnels", "Personal care", ""),
    ("4720", "47", "expense", "other", False, "Formation", "Education", ""),
    ("4730", "47", "expense", "other", False, "Cadeaux & dons", "Gifts & donations", ""),
    ("48", "4", "expense", "other", True, "Équipement", "Equipment", ""),
    ("4800", "48", "expense", "other", False, "Électronique & informatique", "Electronics & IT", ""),
    ("4810", "48", "expense", "other", False, "Mobilier & maison", "Furniture & home", ""),
    ("49", "4", "expense", "other", True, "Frais financiers", "Financial costs", ""),
    ("4900", "49", "expense", "other", False, "Frais bancaires", "Bank fees", "account fees, card fees"),
    ("4910", "49", "expense", "other", False, "Intérêts débiteurs", "Interest paid", ""),
    ("4920", "49", "expense", "other", False, "Différences de change", "FX differences", ""),
    ("4990", "4", "expense", "other", False, "Divers", "Miscellaneous", "only if nothing else fits"),
]

DEFAULT_SETTINGS = {
    "auto_classify": True,  # AI classification of imported entries
    "auto_match": True,  # match imports to manual entries / receipts
    "match_tolerance_days": 7,
    "ai_min_confidence": 0.0,  # below this, AI suggestion is stored but tx stays unclassified
}


def seed_chart(db: Session, ledger: Ledger, locale: str = "fr") -> None:
    by_code: dict[str, Account] = {}
    for code, parent, typ, kind, is_group, fr, en, hint in CHART:
        acc = Account(
            ledger_id=ledger.id,
            code=code,
            name=fr if locale == "fr" else en,
            type=typ,
            kind=kind,
            is_group=is_group,
            currency=ledger.base_currency,
            description=hint or None,
            parent=by_code.get(parent) if parent else None,
        )
        db.add(acc)
        by_code[code] = acc
    db.flush()


def create_ledger(db: Session, name: str, locale: str, currency: str = "CHF") -> Ledger:
    ledger = Ledger(name=name, locale=locale, base_currency=currency, settings=dict(DEFAULT_SETTINGS))
    db.add(ledger)
    db.flush()
    seed_chart(db, ledger, locale)
    return ledger
