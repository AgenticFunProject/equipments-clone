Feature: Inventory catalog and container APIs

  Background:
    Given the seeded equipments service is running

  Scenario: Equipment types can be listed, created, and updated
    Then the equipment type catalog contains 5 entries
    And the equipment type catalog includes "20FT" described as "Standard 20-foot dry container"
    When I create equipment type "45HC" described as "45-foot High Cube" with nominal length "45'" and max payload 29500
    Then the latest response status is 201
    And the latest JSON response has field "code" equal to "45HC"
    When I update equipment type "45hc" description to "45-foot High Cube Updated"
    Then the latest response status is 200
    And the latest JSON response has field "description" equal to "45-foot High Cube Updated"
    And the equipment type catalog contains 6 entries
    And the equipment type catalog includes "45HC" described as "45-foot High Cube Updated"

  Scenario: Equipment type errors are surfaced
    When I try to create equipment type "20FT" described as "Duplicate" with nominal length "20'" and max payload 1
    Then the latest response status is 409
    And the latest error contains "equipment type 20FT already exists"
    When I try to update equipment type "DOES-NOT-EXIST" description to "nope"
    Then the latest response status is 404
    And the latest error contains "equipment type DOES-NOT-EXIST not found"

  Scenario: Containers can be registered, listed, fetched, and overridden
    When I register container "CONU8888888" of type "20FT" at depot "NLRTM-01"
    Then the latest response status is 201
    And the latest container status is "AVAILABLE"
    When I list containers with type "20FT" status "AVAILABLE" depot "NLRTM-01"
    Then the latest response status is 200
    And the latest container list includes container "CONU8888888"
    When I fetch the latest container
    Then the latest response status is 200
    And the latest JSON response has field "containerNumber" equal to "CONU8888888"
    When I manually set the latest container status to "IN_TRANSIT"
    Then the latest container status is "IN_TRANSIT"

  Scenario: Availability reports seeded depot counts
    Then availability at depot "CNSHA-01" shows 3 units of "20FT"
    And availability at depot "CNSHA-01" shows 2 units of "40FT"
    And availability at depot "CNSHA-01" shows 1 units of "40HC"
