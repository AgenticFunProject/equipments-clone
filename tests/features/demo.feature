Feature: Equipments empty database demo

  Scenario: Run the documented demo flow from an empty database
    Given the equipments service starts from an empty sqlite database
    Then the equipment type catalog is empty
    And the container inventory is empty
    And availability at depot "CNSHA-01" is empty
    When I create equipment type "20FT" described as "Standard 20-foot dry container" with nominal length "20'" and max payload 28200
    And I create equipment type "40FT" described as "Standard 40-foot dry container" with nominal length "40'" and max payload 26500
    Then the equipment type catalog contains 2 entries
    When I register container "CONU1234567" of type "20FT" at depot "CNSHA-01"
    And I register container "CONU7654321" of type "20FT" at depot "CNSHA-01"
    And I register container "CONU3000001" of type "40FT" at depot "CNSHA-01"
    Then the container inventory contains 3 entries
    And availability at depot "CNSHA-01" shows 2 units of "20FT"
    And availability at depot "CNSHA-01" shows 1 units of "40FT"
    When I reserve 1 units of "20FT" at depot "CNSHA-01" for booking "BKG-DEMO-0001"
    Then the latest reservation has an assigned container
    And availability at depot "CNSHA-01" shows 1 units of "20FT"
    When I pick up the latest reserved container
    Then the latest container status is "DISPATCHED"
    When I manually set the latest container status to "IN_TRANSIT"
    Then the latest container status is "IN_TRANSIT"
    When I return the latest container
    Then the latest container status is "AVAILABLE"
    And the latest container booking reference is null
    And availability at depot "CNSHA-01" shows 2 units of "20FT"
    When I reserve 1 units of "40FT" at depot "CNSHA-01" for booking "BKG-DEMO-0002"
    And I release booking "BKG-DEMO-0002"
    Then the latest reservation release status is "RELEASED"
    When I reserve 1 units of "20FT" at depot "CNSHA-01" for booking "BKG-DEMO-0003"
    And I pick up the latest reserved container
    And I release booking "BKG-DEMO-0003"
    Then the latest response status is 409
    And the latest error contains "cannot be released after dispatch"
