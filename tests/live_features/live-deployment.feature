Feature: Live Azure deployment contract

  Background:
    Given the live Azure deployment is reachable

  Scenario: Public routes expose health, redirects, and OpenAPI
    When I request GET "/health" without a bearer token
    Then the latest live response status is 200
    And the latest live JSON response has field "status" equal to "ok"
    And the latest live JSON response has field "version" equal to "0.1.0"
    When I request GET "/" without a bearer token
    Then the latest live response status is 302
    And the latest live response redirects to "/playground"
    When I request GET "/openapi.json" without a bearer token
    Then the latest live response status is 200
    And the latest live OpenAPI response title is "Equipments Service API"
    And the latest live OpenAPI response exposes path "/availability"
    And the latest live OpenAPI bearerAuth security scheme has type "http" and scheme "bearer"

  Scenario: Bearer JWT validation and authorization work on the live deployment
    When I request GET "/equipment-types" without a bearer token
    Then the latest live response status is 401
    And the latest live error is "missing bearer token"
    When I request GET "/equipment-types" with a read bearer token
    Then the latest live response status is 200
    When I try to register a unique live container with a read bearer token
    Then the latest live response status is 403
    And the latest live error is "missing required scope equipments:modify"
    When I register a unique live container with an admin bearer token
    Then the latest live response status is 201
    When I request GET "/equipment-types" with a bearer token for audience "wrong-audience"
    Then the latest live response status is 401
    And the latest live error is "bearer token audience is invalid"

  Scenario: Inventory CRUD works on the live deployment
    When I create a unique live equipment type
    Then the latest live response status is 201
    And the latest live response includes the unique live equipment type
    When I update the unique live equipment type description to "Live Deployment Updated"
    Then the latest live response status is 200
    And the latest live JSON response has field "description" equal to "Live Deployment Updated"
    When I register a unique live container for the unique live equipment type
    Then the latest live response status is 201
    And the latest live container status is "AVAILABLE"
    When I list live containers for the unique live equipment type at the unique live depot
    Then the latest live response status is 200
    And the latest live container list includes the unique live container
    When I manually set the latest live container status to "IN_TRANSIT"
    Then the latest live container status is "IN_TRANSIT"

  Scenario: Reservation lifecycle and booking events work on the live deployment
    When I create a unique live equipment type
    And I register 2 live containers for the unique live equipment type
    Then live availability at the unique live depot shows 2 units of the unique live equipment type
    When I reserve 1 unit of the unique live equipment type at the unique live depot
    Then the latest live response status is 201
    And the latest live reservation assigned 1 container
    And the latest live reservation status is "ACTIVE"
    And live availability at the unique live depot shows 1 units of the unique live equipment type
    When I pick up the latest live reserved container
    Then the latest live container status is "DISPATCHED"
    When I return the latest live container
    Then the latest live container status is "AVAILABLE"
    And the latest live container booking reference is null
    And live availability at the unique live depot shows 2 units of the unique live equipment type
    When I reserve 1 unit of the unique live equipment type at the unique live depot for a cancellation booking
    And I receive a live "booking.cancelled" event for the latest live booking
    Then the latest live response status is 200
    And the latest live JSON response has boolean field "processed" equal to true
    And the latest live container status is "AVAILABLE"
    When I reserve 1 unit of the unique live equipment type at the unique live depot for a completed booking
    And I pick up the latest live reserved container
    And I receive a live "booking.completed" event for the latest live booking
    Then the latest live response status is 200
    And the latest live JSON response has boolean field "processed" equal to true
    And the latest live container status is "AVAILABLE"
