Feature: Playground development tools and persistence

  Scenario: Development token generation returns usable bearer tokens
    Given the seeded equipments service is running
    When I generate a development bearer token for subject "playground-user" with read scope
    Then the latest response status is 201
    And the latest response includes a generated bearer token
    And the latest JSON response has field "subject" equal to "playground-user"
    And the latest JSON response has string array field "scopes" containing exactly "equipments:read"
    When I request GET "/availability?depotCode=CNSHA-01" with the latest generated bearer token
    Then the latest response status is 200

  Scenario: SQLite empty first boot persists service state across restart
    Given the equipments service starts from an empty sqlite database
    Then the equipment type catalog is empty
    When I create equipment type "45OT" described as "45-foot Open Top" with nominal length "45'" and max payload 28000
    Then the latest JSON response has persisted local user metadata
    When I restart the service with the same runtime storage and no seeded data
    Then equipment type "45OT" still has the same local user metadata
