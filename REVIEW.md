# REVIEW.md

### 1. `list_employees()`
- **What was wrong:** The pagination skip calculation was incorrect.
- **How I noticed:** Tested page 1 and page 2.
- **How I fixed it:** Corrected the skip calculation.

### 2. `punch_in()`
- **What was wrong:** The call to `compute_late_minutes()` was missing an argument.
- **How I noticed:** Checked the punch-in function call.
- **How I fixed it:** Passed the required argument.

### 3. `punch_in()`
- **What was wrong:** The response included MongoDB's internal `_id`.
- **How I noticed:** Checked the response body.
- **How I fixed it:** Removed `_id` from the response.

### 4. Attendance index
- **What was wrong:** Duplicate attendance records could be created for the same employee and date.
- **How I noticed:** Checked duplicate punch-in behavior.
- **How I fixed it:** Added a unique compound index on `emp_code` and `date`.

### 5. `punch_in()`
- **What was wrong:** Unknown employee codes were not handled correctly.
- **How I noticed:** Tested punch-in with an unknown employee code.
- **How I fixed it:** Added employee existence validation and returned `404`.

### 6. `punch_in()`
- **What was wrong:** Timestamp handling did not consistently convert timestamps to IST and remove microseconds.
- **How I noticed:** Checked timestamp handling.
- **How I fixed it:** Converted timestamps to IST and removed microseconds.

### 7. `compute_late_minutes()`
- **What was wrong:** The calculation did not correctly handle the grace period and overnight shifts.
- **How I noticed:** Tested a 09:41 punch-in for a 09:30 shift.
- **How I fixed it:** Updated the calculation to handle the 10-minute grace period and overnight shifts.

### 8. `compute_overtime()`
- **What was wrong:** Overtime calculation did not correctly handle overnight shifts and the 30-minute minimum.
- **How I noticed:** Tested punch-outs at 06:15, 06:29, 06:30 and 06:45 for a 22:00–06:00 shift.
- **How I fixed it:** Corrected the shift-end calculation and the 30-minute overtime threshold.

### 9. `list_attendance()`
- **What was wrong:** Matching records were loaded into Python, filtered totals were incorrect, and `_id` appeared in the response.
- **How I noticed:** Tested attendance filters, totals and pagination.
- **How I fixed it:** Moved sorting and pagination to MongoDB, corrected filtered counts, validated pagination and removed `_id`.

### Checked and not considered defects
- The unique index on `emp_code` is needed to prevent duplicate employee codes.
- MongoDB's `_id` is useful internally, but it should not appear in API responses.