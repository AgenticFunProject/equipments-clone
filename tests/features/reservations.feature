Feature: Reservations, container lifecycle, and booking events

  Background:
    Given the seeded equipments service is running

  Scenario: Reservations assign available containers atomically
    When I reserve 2 units of "20FT" at depot "CNSHA-01" for booking "BKG-2026-00042"
    Then the latest response status is 201
    And the latest reservation assigned 2 containers
    And the latest reservation status is "ACTIVE"
    And all containers assigned to the latest reservation have status "RESERVED"
    And availability at depot "CNSHA-01" shows 1 units of "20FT"

  Scenario: Insufficient stock leaves inventory unchanged
    When I try to reserve 2 units of "40HC" at depot "CNSHA-01" for booking "BKG-OVER-ASK"
    Then the latest response status is 409
    And the latest error contains "insufficient available 40HC at depot CNSHA-01"
    And availability at depot "CNSHA-01" shows 1 units of "40HC"

  Scenario: Pickup and return enforce lifecycle rules
    When I reserve 1 units of "20FT" at depot "CNSHA-01" for booking "BKG-LC-1"
    And I pick up the latest reserved container
    Then the latest container status is "DISPATCHED"
    When I return the latest container
    Then the latest container status is "AVAILABLE"
    And the latest container booking reference is null
    And availability at depot "CNSHA-01" shows 3 units of "20FT"
    When I try to pick up the latest reserved container
    Then the latest response status is 409
    And the latest error contains "pickup allowed only when status is RESERVED"

  Scenario: Cancelled and completed bookings process events
    When I reserve 1 units of "20FT" at depot "CNSHA-01" for booking "BKG-CANCEL-1"
    And I receive a "booking.cancelled" event for booking "BKG-CANCEL-1"
    Then the latest response status is 200
    And the latest JSON response has boolean field "processed" equal to true
    And the latest container status is "AVAILABLE"
    And the latest container booking reference is null
    When I reserve 1 units of "20FT" at depot "CNSHA-01" for booking "BKG-COMPLETE-1"
    And I pick up the latest reserved container
    And I receive a "booking.completed" event for booking "BKG-COMPLETE-1"
    Then the latest response status is 200
    And the latest JSON response has boolean field "processed" equal to true
    And the latest container status is "AVAILABLE"
