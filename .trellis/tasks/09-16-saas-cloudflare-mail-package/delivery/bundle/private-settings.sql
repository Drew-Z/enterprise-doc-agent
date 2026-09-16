-- Apply once, to a NEW dedicated mailbox database after schema.sql.
-- No addresses, credentials or actual messages are created here.
INSERT INTO settings (key, value) VALUES
  ('db_version', 'v0.0.9'),
  ('user_settings', '{"enable":false,"enableMailVerify":false}'),
  ('email_rule_settings', '{"blockReceiveUnknowAddressEmail":true}'),
  ('verified_address_list', '[]'),
  ('no_limit_send_address_list', '[]');
