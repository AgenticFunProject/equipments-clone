Feature: Public routes

  Background:
    Given the seeded equipments service is running

  Scenario: Health and OpenAPI routes are available without a bearer token
    When I request GET "/health" without a bearer token
    Then the latest response status is 200
    And the latest JSON response has field "status" equal to "ok"
    And the latest JSON response has field "version" equal to the service version
    When I request GET "/openapi.json" without a bearer token
    Then the latest response status is 200
    And the latest response content type starts with "application/json"
    And the latest JSON response has field "openapi" equal to "3.1.0"
    And the latest OpenAPI response title is "Equipments Service API"
    And the latest OpenAPI response exposes path "/availability"
    And the latest OpenAPI response exposes path "/reservations"
    And the latest OpenAPI response exposes path "/events"
    And the latest OpenAPI bearerAuth security scheme has type "http" and scheme "bearer"

  Scenario: Root route sends users to the playground without a bearer token
    When I request GET "/" without a bearer token
    Then the latest response status is 302
    And the latest response redirects to "/playground"

  Scenario: Protected API routes reject anonymous callers
    When I request GET "/equipment-types" without a bearer token
    Then the latest response status is 401
    And the latest error is "missing bearer token"
