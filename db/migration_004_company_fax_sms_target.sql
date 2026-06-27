-- migration_004_company_fax_sms_target.sql

ALTER TABLE company_suggestions
    ADD COLUMN fax VARCHAR(20) NULL AFTER phone,
    ADD COLUMN sms_number_type VARCHAR(10) NOT NULL DEFAULT 'phone' AFTER fax;
