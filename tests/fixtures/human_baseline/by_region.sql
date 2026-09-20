SELECT region, count(*) AS orders, sum(units) AS units, sum(revenue) AS revenue
FROM sales
GROUP BY region
ORDER BY region
