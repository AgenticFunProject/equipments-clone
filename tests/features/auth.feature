Feature: Bearer authentication

  Background:
    Given the seeded equipments service is running

  Scenario: Read and write scopes control protected route access
    When I request GET "/equipment-types" with a read bearer token
    Then the latest response status is 200
    When I try to register container "READ1111111" of type "20FT" at depot "CNSHA-01" with a read bearer token
    Then the latest response status is 403
    And the latest error is "missing required scope equipments:modify"

  Scenario: Admin role authorizes protected routes without equipment scopes
    When I request GET "/equipment-types" with an admin bearer token without equipment scopes
    Then the latest response status is 200
    When I register container "ADMG1111111" of type "20FT" at depot "CNSHA-01" with an admin bearer token without equipment scopes
    Then the latest response status is 201

  Scenario: Users Service admin role does not bypass JWT validation
    When I request GET "/equipment-types" with a Users Service admin bearer token for audience "wrong-audience"
    Then the latest response status is 401
    And the latest error is "bearer token audience is invalid"
    When I request GET "/equipment-types" with a Users Service admin bearer token from issuer "users-service"
    Then the latest response status is 401
    And the latest error is "bearer token issuer is invalid"
    When I request GET "/equipment-types" with an expired Users Service admin bearer token
    Then the latest response status is 401
    And the latest error is "bearer token is expired"
    When I request GET "/equipment-types" with a Users Service admin bearer token that has an invalid signature
    Then the latest response status is 401
    And the latest error is "invalid bearer token signature"

  Scenario: Admin role matching is exact
    When I request GET "/equipment-types" with a bearer token role "Admin" and no equipment scopes
    Then the latest response status is 403
    And the latest error is "missing required scope equipments:read"
    When I request GET "/equipment-types" with a bearer token role "administrator" and no equipment scopes
    Then the latest response status is 403
    And the latest error is "missing required scope equipments:read"
