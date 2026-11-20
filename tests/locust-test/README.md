# Load Testing Results

This directory contains the results of load tests performed on the Store Backend API using **Locust**.

The tests simulated customer, seller, and administrative operations, including product management, shopping carts, order creation, payments, and expected failure scenarios.

## Test Environment

- Backend: FastAPI
- Database: SQLite
- Load testing tool: Locust
- Environment: Local development machine
- Target: `http://127.0.0.1:8001`

## Results

| Metric | 500 Users | 1000 Users |
|---|---:|---:|
| Duration | 3 minutes | 5 minutes |
| Total Requests | 39,994 | 54,702 |
| Failures | 1 | 0 |
| Average RPS | 222 | 182 |
| P50 | 520 ms | 3,600 ms |
| P95 | 1,300 ms | 7,400 ms |
| P99 | 2,300 ms | 10,000 ms |

## Observations

- The 500-user test recorded one client-side request failure.
- The 1000-user test recorded no failures.
- Response times increased significantly as concurrency increased.
- Average throughput decreased despite the higher number of virtual users.
- Write-intensive operations, including order processing and product updates, experienced higher latency.

## Possible Bottlenecks

SQLite write contention, database locking, application processing overhead, and limited local machine resources are possible causes.

Further investigation is required to identify the exact bottleneck.

## Conclusion

The API completed the 1000-user load test without recorded failures. However, the significant increase in P95 and P99 response times indicates performance degradation under heavy load.

These results represent a local test environment and should not be interpreted as production capacity guarantees.


## see 
[500 Users Report](docs/500 users.html)
[1000 Users Report](docs/1000 users.html)
