

1. **Indexes.** Which indexes did you create, and which query or guarantee does each one serve? Which one did you consider and reject?
I created a unique index on emp_code so two employees cannot have the same code. I also created a unique index on emp_code and date in attendance logs so one employee cannot have two attendance records for the same day. I added other indexes for date, employee joining date and department to make queries faster. I would add more indexes only if testing shows they are needed.

2. **Punch-in race.** Two identical punch-in requests arrive at the same instant. Walk through exactly what happens in your code.
If two punch-in requests come at the same time, both may try to create a record. The unique index allows only one record to be saved. The other request should get a duplicate key error and return 409 Conflict.

3. **Ties.** Two employees are tied on late minutes at the cutoff of `limit`. What does your leaderboard return, and why?
If two employees have the same late minutes, they should get the same rank. The next rank is skipped. I apply the limit after ranking so tied employees at the cutoff are not missed. If they have the same rank, I sort them by employee code.

4. **Headcount.** How does your department summary make sure employees with zero logs are counted?
I start the department summary from the employees collection and then look for their attendance records. This way, employees with no attendance records are still counted. Employees who joined after that month are not included.

5. **One thing you would change** if this had to serve 100x the data.
If the data becomes 100 times bigger, I would test query performance with more data, improve indexes where needed, and consider storing summary results to make analytics faster.
