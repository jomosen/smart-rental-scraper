-- Insertar una fila de prueba en cada partición
INSERT INTO providers (code, display_name, scraper_key, default_currency)
VALUES ('provider_test', 'Test Provider', 'test_scraper', 'EUR');

INSERT INTO provider_locations (provider_id, location_code, location_name)
VALUES ((SELECT id FROM providers WHERE code = 'provider_test'), 'TST', 'Test Location');

INSERT INTO provider_rates (provider_id, rate_code, rate_name)
VALUES ((SELECT id FROM providers WHERE code = 'provider_test'), 'standard', 'Standard');

INSERT INTO provider_vehicle_groups
  (provider_id, provider_location_id, provider_rate_id, external_code, external_name)
VALUES (
  (SELECT id FROM providers WHERE code = 'provider_test'),
  (SELECT id FROM provider_locations WHERE location_code = 'TST'),
  (SELECT id FROM provider_rates WHERE rate_code = 'standard'),
  'TEST_GROUP', 'Test Group'
);

INSERT INTO scrape_runs
  (provider_id, provider_location_id, provider_rate_id, status)
VALUES (
  (SELECT id FROM providers WHERE code = 'provider_test'),
  (SELECT id FROM provider_locations WHERE location_code = 'TST'),
  (SELECT id FROM provider_rates WHERE rate_code = 'standard'),
  'success'
);

-- Insertar observación en mayo (debe ir a price_observations_2026_05)
INSERT INTO price_observations
  (provider_id, provider_location_id, provider_rate_id, provider_vehicle_group_id,
   scrape_run_id, pickup_date, duration_days, price_per_day, total_price, currency, observed_at)
SELECT
  p.id, pl.id, pr.id, pvg.id, sr.id,
  '2026-07-15', 7, 45.50, 318.50, 'EUR', '2026-05-15 10:00:00+00'
FROM providers p
JOIN provider_locations pl ON pl.provider_id = p.id
JOIN provider_rates pr ON pr.provider_id = p.id
JOIN provider_vehicle_groups pvg ON pvg.provider_id = p.id
JOIN scrape_runs sr ON sr.provider_id = p.id
WHERE p.code = 'provider_test';

-- Verificar que fue a la partición correcta
SELECT tableoid::regclass, * FROM price_observations;
-- Debe mostrar la fila con tableoid = 'price_observations_2026_05'

-- Limpieza
DELETE FROM price_observations;
DELETE FROM scrape_runs;
DELETE FROM provider_vehicle_groups;
DELETE FROM provider_rates;
DELETE FROM provider_locations;
DELETE FROM providers WHERE code = 'provider_test';