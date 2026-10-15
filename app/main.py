"""
Employee Attendance & Analytics API - STARTER

Run:  uvicorn app.main:app --port 8000
Env:  MONGO_URI, MONGO_DB (a local .env is loaded for convenience)

This file was written quickly by a colleague who has left the company. The happy path
works, but nobody has reviewed it. Read PROBLEM_STATEMENT.docx for what is expected of you,
openapi.yaml for the contract and DATA_MODEL.md for what is stored in MongoDB.
"""
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from pymongo import MongoClient, ASCENDING
from pymongo.errors import DuplicateKeyError, PyMongoError

from decimal import Decimal, ROUND_HALF_UP
from bson import json_util






load_dotenv()

client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"))
db = client[os.getenv("MONGO_DB", "attendance_db")]

IST = timezone(timedelta(hours=5, minutes=30))

app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0")

# ---------------------------------------------------------------------------
# MongoDB indexes
# ---------------------------------------------------------------------------

def create_indexes():
    # Every employee must have a unique employee code.
    db.employees.create_index(
        [("emp_code", ASCENDING)],
        unique=True,
        name="unique_employee_code"
    )

    # An employee can have only one attendance record per date.
    db.attendance_logs.create_index(
        [
            ("emp_code", ASCENDING),
            ("date", ASCENDING)
        ],
        unique=True,
        name="unique_employee_attendance_date"
    )
    db.attendance_logs.create_index(
    [("date", -1)],
    name="idx_attendance_date"
    )

    db.attendance_logs.create_index(
    [("emp_code", 1), ("date", -1)],
    name="idx_attendance_emp_date"
    )
    db.employees.create_index(
    [("joined_on", 1)],
    name="idx_employees_joined_on"
    )
    db.employees.create_index(
    [("department", 1)],
    name="idx_employees_department"
    )

@app.on_event("startup")
def startup_event():
    create_indexes()    


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def compute_late_minutes(
    punch_in: datetime,
    shift_start: str,
    shift_end: str
) -> int:
    start_h, start_m = map(int, shift_start.split(":"))
    end_h, end_m = map(int, shift_end.split(":"))

    # Determine whether the shift crosses midnight
    overnight = (end_h, end_m) <= (start_h, start_m)

    # Find the correct shift-start date for overnight shifts
    shift_date = punch_in.date()

    if overnight and (punch_in.hour, punch_in.minute) < (end_h, end_m):
        shift_date -= timedelta(days=1)

    shift_start_dt = datetime(
        shift_date.year,
        shift_date.month,
        shift_date.day,
        start_h,
        start_m,
        tzinfo=punch_in.tzinfo
    )

    # Calculate minutes late relative to scheduled shift start
    minutes_late = int(
        (punch_in - shift_start_dt).total_seconds() // 60
    )

    # Apply the 10-minute grace period
    if minutes_late <= 10:
        return 0

    return minutes_late




def compute_work_hours(
    punch_in: datetime,
    punch_out: datetime
) -> float:
    seconds = Decimal(
        str((punch_out - punch_in).total_seconds())
    )

    hours = seconds / Decimal("3600")

    rounded_hours = hours.quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP
    )

    return float(rounded_hours)




def compute_overtime(
    punch_out: datetime,
    shift_start: str,
    shift_end: str,
    date_str: str
) -> int:
    start_h, start_m = map(int, shift_start.split(":"))
    end_h, end_m = map(int, shift_end.split(":"))

    # Determine whether the shift crosses midnight
    overnight = (end_h, end_m) <= (start_h, start_m)

    # Create the scheduled shift-end time in IST
    shift_date = datetime.fromisoformat(date_str).date()

    shift_end_dt = datetime(
        shift_date.year,
        shift_date.month,
        shift_date.day,
        end_h,
        end_m,
        tzinfo=IST
    )

    # For overnight shifts, the shift ends on the next day
    if overnight:
        shift_end_dt += timedelta(days=1)

    # Calculate minutes worked beyond the scheduled shift end
    overtime_minutes = int(
        (punch_out - shift_end_dt).total_seconds() // 60
    )

    # Overtime counts only when it reaches 30 minutes
    if overtime_minutes < 30:
        return 0

    return overtime_minutes



# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
class EmployeeIn(BaseModel):
    emp_code: str
    name: str
    email: str
    department: str
    shift_start: str = "09:30"
    shift_end: str = "18:30"
    joined_on: str


class PunchInIn(BaseModel):
    emp_code: str
    punched_at: Optional[int] = None
    status: str = "PRESENT"






class RegularizeIn(BaseModel):
    status: Optional[str] = None
    punch_in: Optional[int] = None
    punch_out: Optional[int] = None
    reason: str = Field(min_length=5, max_length=200)
    regularized_by: str = Field(min_length=1, max_length=50)

    @field_validator("status")
    @classmethod
    def validate_status(cls, value):
        allowed = {"PRESENT", "ABSENT", "LEAVE", "WFH", "ON_DUTY"}
        if value is not None and value not in allowed:
            raise ValueError("Invalid attendance status")
        return value

    @field_validator("punch_in", "punch_out")
    @classmethod
    def validate_timestamp(cls, value):
        if value is not None and not 100000000000 <= value <= 4102444800000:
            raise ValueError("Timestamp must be epoch milliseconds")
        return value





# --------------------------------------------------------------------------- #
# Endpoints provided
# --------------------------------------------------------------------------- #

@app.get("/health")
def health():
    try:
        db.command("ping")
        return {"status": "ok"}
    except PyMongoError:
        raise HTTPException(status_code=503, detail="Database unavailable")



@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    if db.employees.find_one({"emp_code": body.emp_code}):
        raise HTTPException(409, "emp_code already exists")
    doc = body.model_dump()
    doc["created_at"] = datetime.now()
    db.employees.insert_one(doc)
    doc.pop("_id", None)
    return doc



@app.get("/employees")
def list_employees(
    department: Optional[str] = None,
    page: int = 1,
    page_size: int = 20
):
    if page < 1:
        raise HTTPException(status_code=422, detail="Page must be at least 1")

    if page_size < 1 or page_size > 100:
        raise HTTPException(
            status_code=422,
            detail="Page size must be between 1 and 100"
        )

    q = {}
    if department:
        q["department"] = department

    skip = (page - 1) * page_size

    total = db.employees.count_documents(q)

    items = list(
        db.employees.find(q, {"_id": 0})
        .sort("emp_code", 1)
        .skip(skip)
        .limit(page_size)
    )

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size
    }



@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    # 1. Check whether the employee exists
    emp = db.employees.find_one({"emp_code": body.emp_code})

    if emp is None:
        raise HTTPException(
            status_code=404,
            detail="Employee not found"
        )

    # 2. Get the punch-in time and convert it to IST
    if body.punched_at is not None:
        ts = datetime.fromtimestamp(
            body.punched_at / 1000,
            tz=timezone.utc
        ).astimezone(IST)
    else:
        ts = datetime.now(IST)

    # Remove microseconds as required
    ts = ts.replace(microsecond=0)

    # The attendance date must be the IST calendar date
    date_str = ts.date().isoformat()

    # 3. Calculate late minutes using the employee's shift
    late_minutes = compute_late_minutes(
        ts,
        emp["shift_start"],
        emp["shift_end"]
    )

    # 4. Prepare the attendance document
    doc = {
        "emp_code": body.emp_code,
        "date": date_str,
        "status": body.status,
        "punch_in": ts,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": late_minutes,
        "overtime_minutes": 0,
        "half_day": False,
        "history": []
    }

    # 5. Insert atomically; the unique index prevents duplicate attendance
    try:
        db.attendance_logs.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(
            status_code=409,
            detail="Already punched in for this date"
        )

    # 6. Remove MongoDB's internal ID before returning the response
    doc.pop("_id", None)

    return doc




@app.post("/attendance/punch-out")
def punch_out(emp_code: str, date_str: str):
    now = datetime.now(IST).replace(microsecond=0)

    attendance = db.attendance_logs.find_one({
        "emp_code": emp_code,
        "date": date_str
    })

    if attendance is None:
        raise HTTPException(
            status_code=404,
            detail="No punch-in record found for this employee and date"
        )

    if attendance.get("punch_out") is not None:
        raise HTTPException(
            status_code=409,
            detail="Already punched out"
        )

    employee = db.employees.find_one({"emp_code": emp_code})

    if employee is None:
        raise HTTPException(
            status_code=404,
            detail="Employee not found"
        )

    punch_in_time = attendance["punch_in"]

    # MongoDB returns naive datetimes in UTC by default
    if punch_in_time.tzinfo is None:
        punch_in_time = punch_in_time.replace(tzinfo=timezone.utc)

    punch_in_time = punch_in_time.astimezone(IST)

    # Prevent punch-out before punch-in
    if now < punch_in_time:
        raise HTTPException(
            status_code=400,
            detail="Punch-out time cannot be earlier than punch-in time"
        )

    # Reject attendance sessions left open for more than 24 hours
    if now - punch_in_time > timedelta(hours=24):
        raise HTTPException(
            status_code=400,
            detail="Attendance session is too old to punch out"
        )

    work_hours = compute_work_hours(punch_in_time, now)

    overtime_minutes = compute_overtime(
        now,
        employee["shift_start"],
        employee["shift_end"],
        date_str
    )

    half_day = work_hours < 4.50

    result = db.attendance_logs.update_one(
        {
            "emp_code": emp_code,
            "date": date_str,
            "punch_out": None
        },
        {
            "$set": {
                "punch_out": now,
                "work_hours": work_hours,
                "overtime_minutes": overtime_minutes,
                "half_day": half_day
            }
        }
    )

    if result.modified_count != 1:
        raise HTTPException(
            status_code=409,
            detail="Already punched out"
        )

    return db.attendance_logs.find_one(
        {"emp_code": emp_code, "date": date_str},
        {"_id": 0}
    )






@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    page: int = 1,
    page_size: int = 20
):
    if page < 1:
        raise HTTPException(
            status_code=400,
            detail="page must be at least 1"
        )

    if page_size < 1 or page_size > 100:
        raise HTTPException(
            status_code=400,
            detail="page_size must be between 1 and 100"
        )

    query = {}

    if emp_code:
        query["emp_code"] = emp_code

    if date_from or date_to:
        query["date"] = {}

        if date_from:
            query["date"]["$gte"] = date_from

        if date_to:
            query["date"]["$lte"] = date_to

    if status:
        query["status"] = status

    total = db.attendance_logs.count_documents(query)

    items = list(
        db.attendance_logs.find(query, {"_id": 0})
        .sort("date", -1)
        .skip((page - 1) * page_size)
        .limit(page_size)
    )

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size
    }


@app.patch("/attendance/{emp_code}/{date}")
def regularize_attendance(
    emp_code: str,
    date: str,
    body: RegularizeIn
):
    from datetime import datetime, timezone

    # Validate the attendance date
    try:
        record_date = datetime.strptime(date, "%Y-%m-%d").date()
        if record_date.isoformat() != date:
            raise ValueError
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid date format")

    employee = db.employees.find_one({"emp_code": emp_code})
    if employee is None:
        raise HTTPException(status_code=404, detail="Employee not found")

    attendance = db.attendance_logs.find_one({
        "emp_code": emp_code,
        "date": date
    })
    if attendance is None:
        raise HTTPException(status_code=404, detail="Attendance record not found")

    # Use only fields explicitly supplied by the caller
    changes_requested = body.model_fields_set - {"reason", "regularized_by"}
    if not changes_requested:
        raise HTTPException(status_code=422, detail="No attendance changes supplied")

    old_status = attendance.get("status", "PRESENT")
    old_in = attendance.get("punch_in")
    old_out = attendance.get("punch_out")

    new_status = body.status if "status" in changes_requested else old_status
    new_in = body.punch_in if "punch_in" in changes_requested else old_in
    new_out = body.punch_out if "punch_out" in changes_requested else old_out

    absent_statuses = {"ABSENT", "LEAVE"}
    presence_statuses = {"PRESENT", "WFH", "ON_DUTY"}

    # ABSENT and LEAVE clear punch times and cannot receive supplied times
    if new_status in absent_statuses:
        if "punch_in" in changes_requested or "punch_out" in changes_requested:
            raise HTTPException(
                status_code=422,
                detail="Cannot supply punch times with ABSENT or LEAVE"
            )
        new_in = None
        new_out = None
    elif new_status in presence_statuses:
        if new_in is None:
            raise HTTPException(
                status_code=422,
                detail="Presence status requires a punch-in time"
            )

    def as_ist(value):
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value / 1000, tz=timezone.utc).astimezone(IST)
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(IST)

    punch_in_dt = as_ist(new_in)
    punch_out_dt = as_ist(new_out)

    # Punch-in must be on the record's IST attendance date
    if punch_in_dt is not None and punch_in_dt.date() != record_date:
        raise HTTPException(
            status_code=422,
            detail="Punch-in must be on the attendance date"
        )

    # Punch-out must follow punch-in and be within 24 hours
    if punch_out_dt is not None:
        if punch_in_dt is None or punch_out_dt <= punch_in_dt:
            raise HTTPException(
                status_code=422,
                detail="Punch-out must be after punch-in"
            )
        if punch_out_dt - punch_in_dt > timedelta(hours=24):
            raise HTTPException(
                status_code=422,
                detail="Punch-out must be within 24 hours of punch-in"
            )

    # Compare requested values to current values
    def same_instant(a, b):
        if a is None or b is None:
            return a is None and b is None
        return as_ist(a) == as_ist(b)

    actual_changes = {}

    if new_status != old_status:
        actual_changes["status"] = {"from": old_status, "to": new_status}

    if not same_instant(new_in, old_in):
        actual_changes["punch_in"] = {
            "from": old_in,
            "to": punch_in_dt
        }

    if not same_instant(new_out, old_out):
        actual_changes["punch_out"] = {
            "from": old_out,
            "to": punch_out_dt
        }

    if not actual_changes:
        raise HTTPException(
            status_code=422,
            detail="Request does not change the attendance record"
        )

    # Recompute derived values from the final times
    late_minutes = 0
    work_hours = None
    overtime_minutes = 0
    half_day = False

    if new_in is not None:
        late_minutes = compute_late_minutes(
            punch_in_dt,
            employee["shift_start"],
            employee["shift_end"]
        )

    if punch_in_dt is not None and punch_out_dt is not None:
        work_hours = compute_work_hours(punch_in_dt, punch_out_dt)
        overtime_minutes = compute_overtime(
            punch_out_dt,
            employee["shift_start"],
            employee["shift_end"],
            date
        )
        half_day = work_hours < 4.50

    # Append history using a conditional atomic update.
    # Comparing the old values prevents concurrent corrections from
    # silently overwriting each other's history.
    history_entry = {
        "regularized_by": body.regularized_by,
        "reason": body.reason,
        "regularized_at": datetime.now(IST).replace(microsecond=0),
        "changes": actual_changes
    }

    filter_query = {
        "emp_code": emp_code,
        "date": date,
        "status": old_status,
        "punch_in": attendance.get("punch_in"),
        "punch_out": attendance.get("punch_out")
    }

    update_result = db.attendance_logs.update_one(
        filter_query,
        {
            "$set": {
                "status": new_status,
                "punch_in": punch_in_dt,
                "punch_out": punch_out_dt,
                "late_minutes": late_minutes,
                "work_hours": work_hours,
                "overtime_minutes": overtime_minutes,
                "half_day": half_day
            },
            "$push": {"history": history_entry}
        }
    )

    if update_result.modified_count != 1:
        raise HTTPException(
            status_code=409,
            detail="Attendance record changed concurrently; retry your request"
        )

    return db.attendance_logs.find_one(
        {"emp_code": emp_code, "date": date},
        {"_id": 0}
    )


@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(emp_code: str, month: str):
    import re
    import calendar
    from datetime import date

    # Validate YYYY-MM
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
        raise HTTPException(
            status_code=422,
            detail="Month must be in YYYY-MM format"
        )

    employee = db.employees.find_one({"emp_code": emp_code})
    if employee is None:
        raise HTTPException(status_code=404, detail="Employee not found")

    year, month_number = map(int, month.split("-"))
    first_day = date(year, month_number, 1)
    last_day = date(
        year,
        month_number,
        calendar.monthrange(year, month_number)[1]
    )

    working_days = sum(
        1
        for day in range(1, last_day.day + 1)
        if date(year, month_number, day).weekday() < 5
    )

    pipeline = [
        {
            "$match": {
                "emp_code": emp_code,
                "date": {"$gte": first_day.isoformat(), "$lte": last_day.isoformat()}
            }
        },
        {
            "$group": {
                "_id": None,
                "present_days": {
                    "$sum": {
                        "$cond": [
                            {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                            {
                                "$cond": [
                                    {"$eq": ["$half_day", True]},
                                    0.5,
                                    1
                                ]
                            },
                            0
                        ]
                    }
                },
                "leave_days": {
                    "$sum": {
                        "$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]
                    }
                },
                "late_count": {
                    "$sum": {
                        "$cond": [{"$gt": ["$late_minutes", 0]}, 1, 0]
                    }
                },
                "total_late_minutes": {"$sum": "$late_minutes"},
                "total_overtime_minutes": {"$sum": "$overtime_minutes"}
            }
        }
    ]

    result = list(db.attendance_logs.aggregate(pipeline))
    totals = result[0] if result else {}

    present_days = totals.get("present_days", 0)
    attendance_pct = (
        round(present_days / working_days * 100, 2)
        if working_days > 0 else None
    )

    return {
        "emp_code": emp_code,
        "month": month,
        "working_days": working_days,
        "present_days": present_days,
        "leave_days": totals.get("leave_days", 0),
        "late_count": totals.get("late_count", 0),
        "total_late_minutes": totals.get("total_late_minutes", 0),
        "total_overtime_minutes": totals.get("total_overtime_minutes", 0),
        "attendance_pct": attendance_pct
    }



@app.get("/analytics/departments/summary")
def department_summary(month: str):
    import re
    import calendar
    from datetime import date
    from decimal import Decimal, ROUND_HALF_UP

    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
        raise HTTPException(
            status_code=422,
            detail="Month must be in YYYY-MM format"
        )

    year, month_number = map(int, month.split("-"))
    month_start = date(year, month_number, 1).isoformat()
    month_end = date(
        year,
        month_number,
        calendar.monthrange(year, month_number)[1]
    ).isoformat()

    pipeline = [
        {
            "$match": {
                "joined_on": {"$lte": month_end}
            }
        },
        {
            "$lookup": {
                "from": "attendance_logs",
                "let": {"code": "$emp_code", "dept": "$department"},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {"$eq": ["$emp_code", "$$code"]},
                                    {"$gte": ["$date", month_start]},
                                    {"$lte": ["$date", month_end]}
                                ]
                            }
                        }
                    }
                ],
                "as": "logs"
            }
        },
        {
            "$unwind": {
                "path": "$logs",
                "preserveNullAndEmptyArrays": True
            }
        },
        {
            "$group": {
                "_id": "$department",
                "headcount_codes": {"$addToSet": "$emp_code"},
                "present_days": {
                    "$sum": {
                        "$cond": [
                            {
                                "$in": [
                                    "$logs.status",
                                    ["PRESENT", "WFH", "ON_DUTY"]
                                ]
                            },
                            {
                                "$cond": [
                                    {"$eq": ["$logs.half_day", True]},
                                    0.5,
                                    1
                                ]
                            },
                            0
                        ]
                    }
                },
                "work_hours_values": {
                    "$push": {
                        "$cond": [
                            {
                                "$and": [
                                    {
                                        "$in": [
                                            "$logs.status",
                                            ["PRESENT", "WFH", "ON_DUTY"]
                                        ]
                                    },
                                    {"$ne": ["$logs.work_hours", None]}
                                ]
                            },
                            "$logs.work_hours",
                            None
                        ]
                    }
                },
                "late_count": {
                    "$sum": {
                        "$cond": [
                            {"$gt": ["$logs.late_minutes", 0]},
                            1,
                            0
                        ]
                    }
                },
                "total_late_minutes": {"$sum": "$logs.late_minutes"},
                "leave_count": {
                    "$sum": {
                        "$cond": [
                            {"$eq": ["$logs.status", "LEAVE"]},
                            1,
                            0
                        ]
                    }
                },
                "on_duty_count": {
                    "$sum": {
                        "$cond": [
                            {"$eq": ["$logs.status", "ON_DUTY"]},
                            1,
                            0
                        ]
                    }
                }
            }
        },
        {
            "$project": {
                "_id": 0,
                "department": "$_id",
                "headcount": {"$size": "$headcount_codes"},
                "present_days": 1,
                "avg_work_hours": {
                    "$avg": "$work_hours_values"
                },
                "late_count": 1,
                "total_late_minutes": 1,
                "leave_count": 1,
                "on_duty_count": 1
            }
        },
        {"$match": {"headcount": {"$gt": 0}}},
        {"$sort": {"department": 1}}
    ]

    items = list(db.employees.aggregate(pipeline))

    # Apply explicit half-up rounding to averages
    for item in items:
        value = item["avg_work_hours"]
        if value is not None:
            item["avg_work_hours"] = float(
                Decimal(str(value)).quantize(
                    Decimal("0.01"),
                    rounding=ROUND_HALF_UP
                )
            )

    return {"month": month, "items": items}



@app.get("/analytics/leaderboard/late")
def late_leaderboard(
    month: str,
    limit: int = 10,
    department: Optional[str] = None
):
    import re
    import calendar
    from datetime import date

    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
        raise HTTPException(
            status_code=422,
            detail="Month must be in YYYY-MM format"
        )

    if not 1 <= limit <= 50:
        raise HTTPException(
            status_code=422,
            detail="Limit must be between 1 and 50"
        )

    year, month_number = map(int, month.split("-"))
    month_start = date(year, month_number, 1).isoformat()
    month_end = date(
        year,
        month_number,
        calendar.monthrange(year, month_number)[1]
    ).isoformat()

    # Match attendance records for the requested month and sum lateness
    pipeline = [
        {
            "$match": {
                "date": {"$gte": month_start, "$lte": month_end},
                "late_minutes": {"$gt": 0}
            }
        },
        {
            "$group": {
                "_id": "$emp_code",
                "total_late_minutes": {"$sum": "$late_minutes"},
                "late_count": {"$sum": 1}
            }
        },
        {"$match": {"total_late_minutes": {"$gt": 0}}},
        {
            "$lookup": {
                "from": "employees",
                "localField": "_id",
                "foreignField": "emp_code",
                "as": "employee"
            }
        },
        {"$unwind": "$employee"},
    ]

    if department:
        pipeline.append({
            "$match": {"employee.department": department}
        })

    pipeline.extend([
        {
            "$setWindowFields": {
                "sortBy": {"total_late_minutes": -1},
                "output": {
                    "rank": {"$rank": {}}
                }
            }
        },
        {"$match": {"rank": {"$lte": limit}}},
        {
            "$sort": {
                "total_late_minutes": -1,
                "_id": 1
            }
        },
        {
            "$project": {
                "_id": 0,
                "rank": 1,
                "emp_code": "$_id",
                "name": "$employee.name",
                "department": "$employee.department",
                "total_late_minutes": 1,
                "late_count": 1
            }
        }
    ])

    items = list(db.attendance_logs.aggregate(pipeline))

    return {
        "month": month,
        "items": items
    }



@app.get("/analytics/departments/{department}/trend")
def department_trend(department: str, from_date: str, to: str):
    import re
    from datetime import date, timedelta

    # Validate dates
    try:
        start = date.fromisoformat(from_date)
        end = date.fromisoformat(to)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="Invalid date format"
        )

    if start.isoformat() != from_date or end.isoformat() != to:
        raise HTTPException(
            status_code=422,
            detail="Dates must use YYYY-MM-DD"
        )

    days = (end - start).days + 1

    if days <= 0 or days > 92:
        raise HTTPException(
            status_code=422,
            detail="Date range must be between 1 and 92 days"
        )

    employees = list(db.employees.find(
        {"department": department},
        {"_id": 0, "emp_code": 1, "name": 1, "joined_on": 1}
    ))

    if not employees:
        raise HTTPException(
            status_code=404,
            detail="Department not found"
        )

    # Generate the calendar in MongoDB and look up daily attendance.
    pipeline = [
        {
            "$group": {
                "_id": None,
                "employees": {
                    "$push": {
                        "emp_code": "$emp_code",
                        "joined_on": "$joined_on"
                    }
                }
            }
        },
        {
            "$set": {
                "days": {
                    "$map": {
                        "input": {"$range": [0, days]},
                        "as": "i",
                        "in": {
                            "$dateToString": {
                                "format": "%Y-%m-%d",
                                "date": {
                                    "$dateAdd": {
                                        "startDate": {
                                            "$dateFromString": {
                                                "dateString": from_date
                                            }
                                        },
                                        "unit": "day",
                                        "amount": "$$i"
                                    }
                                }
                            }
                        }
                    }
                }
            }
        },
        {"$unwind": "$days"},
        {
            "$set": {
                "date": "$days",
                "headcount": {
                    "$size": {
                        "$filter": {
                            "input": "$employees",
                            "as": "e",
                            "cond": {
                                "$lte": ["$$e.joined_on", "$days"]
                            }
                        }
                    }
                },
                "is_working_day": {
                    "$and": [
                        {
                            "$gt": [
                                {
                                    "$dayOfWeek": {
                                        "$dateFromString": {
                                            "dateString": "$days"
                                        }
                                    }
                                },
                                1
                            ]
                        },
                        {
                            "$lt": [
                                {
                                    "$dayOfWeek": {
                                        "$dateFromString": {
                                            "dateString": "$days"
                                        }
                                    }
                                },
                                7
                            ]
                        }
                    ]
                }
            }
        },
        {
            "$lookup": {
                "from": "attendance_logs",
                "let": {
                    "day": "$date",
                    "codes": "$employees.emp_code"
                },
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {"$eq": ["$date", "$$day"]},
                                    {"$in": ["$emp_code", "$$codes"]}
                                ]
                            }
                        }
                    }
                ],
                "as": "logs"
            }
        },
        {
            "$set": {
                "present_count": {
                    "$sum": {
                        "$map": {
                            "input": {
                                "$filter": {
                                    "input": "$logs",
                                    "as": "log",
                                    "cond": {
                                        "$in": [
                                            "$$log.status",
                                            ["PRESENT", "WFH", "ON_DUTY"]
                                        ]
                                    }
                                }
                            },
                            "as": "log",
                            "in": {
                                "$cond": [
                                    {"$eq": ["$$log.half_day", True]},
                                    0.5,
                                    1
                                ]
                            }
                        }
                    }
                },
                "late_count": {
                    "$size": {
                        "$filter": {
                            "input": "$logs",
                            "as": "log",
                            "cond": {
                                "$gt": ["$$log.late_minutes", 0]
                            }
                        }
                    }
                }
            }
        },
        {
            "$set": {
                "attendance_rate": {
                    "$cond": [
                        {
                            "$and": [
                                "$is_working_day",
                                {"$gt": ["$headcount", 0]}
                            ]
                        },
                        {
                            "$divide": [
                                {
                                    "$floor": {
                                        "$add": [
                                            {
                                                "$multiply": [
                                                    {
                                                        "$divide": [
                                                            "$present_count",
                                                            "$headcount"
                                                        ]
                                                    },
                                                    10000
                                                ]
                                            },
                                            0.5
                                        ]
                                    }
                                },
                                10000
                            ]
                        },
                        None
                    ]
                }
            }
        },
        {
            "$setWindowFields": {
                "sortBy": {"date": 1},
                "output": {
                    "moving_avg_raw": {
                        "$avg": "$attendance_rate",
                        "window": {"documents": [-6, 0]}
                    }
                }
            }
        },
        {
            "$project": {
                "_id": 0,
                "date": 1,
                "is_working_day": 1,
                "headcount": 1,
                "present_count": 1,
                "late_count": 1,
                "attendance_rate": 1,
                "moving_avg_7d": {
                    "$cond": [
                        {"$eq": ["$moving_avg_raw", None]},
                        None,
                        {
                            "$divide": [
                                {
                                    "$floor": {
                                        "$add": [
                                            {
                                                "$multiply": [
                                                    "$moving_avg_raw",
                                                    10000
                                                ]
                                            },
                                            0.5
                                        ]
                                    }
                                },
                                10000
                            ]
                        }
                    ]
                }
            }
        },
        {"$sort": {"date": 1}}
    ]

    # Run the pipeline for this department.
    items = list(db.employees.aggregate([
        {"$match": {"department": department}},
        *pipeline
    ]))

    return {
        "department": department,
        "items": items
    }




@app.get("/admin/explain/{endpoint}")
def explain_endpoint(
    endpoint: str,
    emp_code: Optional[str] = None,
    month: Optional[str] = None,
    department: Optional[str] = None,
    limit: int = 10,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    from_date: Optional[str] = None,
    to: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
):
    import re
    import calendar
    from datetime import date

    allowed = {
        "attendance_list",
        "employee_monthly",
        "department_summary",
        "late_leaderboard",
        "department_trend",
    }

    if endpoint not in allowed:
        raise HTTPException(status_code=422, detail="Invalid endpoint")

    if page < 1 or page_size < 1 or page_size > 100:
        raise HTTPException(status_code=422, detail="Invalid pagination")

    try:
        if endpoint == "attendance_list":
            query = {}

            if emp_code:
                query["emp_code"] = emp_code

            if date_from or date_to:
                query["date"] = {}
                if date_from:
                    query["date"]["$gte"] = date_from
                if date_to:
                    query["date"]["$lte"] = date_to

            if status:
                query["status"] = status

            explain = db.command({
                "explain": {
                    "find": "attendance_logs",
                    "filter": query,
                    "sort": {"date": -1},
                    "skip": (page - 1) * page_size,
                    "limit": page_size,
                },
                "verbosity": "executionStats",
            })
            collection = "attendance_logs"

        elif endpoint == "employee_monthly":
            if not emp_code or not month:
                raise HTTPException(
                    status_code=422,
                    detail="emp_code and month are required"
                )

            if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
                raise HTTPException(
                    status_code=422,
                    detail="Month must be in YYYY-MM format"
                )

            year, month_number = map(int, month.split("-"))
            first_day = date(year, month_number, 1).isoformat()
            last_day = date(
                year,
                month_number,
                calendar.monthrange(year, month_number)[1]
            ).isoformat()

            pipeline = [
                {
                    "$match": {
                        "emp_code": emp_code,
                        "date": {"$gte": first_day, "$lte": last_day}
                    }
                },
                {
                    "$group": {
                        "_id": None,
                        "present_days": {
                            "$sum": {
                                "$cond": [
                                    {"$in": [
                                        "$status",
                                        ["PRESENT", "WFH", "ON_DUTY"]
                                    ]},
                                    {
                                        "$cond": [
                                            {"$eq": ["$half_day", True]},
                                            0.5,
                                            1
                                        ]
                                    },
                                    0
                                ]
                            }
                        },
                        "leave_days": {
                            "$sum": {
                                "$cond": [
                                    {"$eq": ["$status", "LEAVE"]},
                                    1,
                                    0
                                ]
                            }
                        },
                        "late_count": {
                            "$sum": {
                                "$cond": [
                                    {"$gt": ["$late_minutes", 0]},
                                    1,
                                    0
                                ]
                            }
                        },
                        "total_late_minutes": {"$sum": "$late_minutes"},
                        "total_overtime_minutes": {
                            "$sum": "$overtime_minutes"
                        }
                    }
                }
            ]

            explain = db.command({
                "explain": {
                    "aggregate": "attendance_logs",
                    "pipeline": pipeline,
                    "cursor": {}
                },
                "verbosity": "executionStats",
            })
            collection = "attendance_logs"

        elif endpoint == "department_summary":
            if not month:
                raise HTTPException(
                    status_code=422,
                    detail="month is required"
                )

            if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
                raise HTTPException(
                    status_code=422,
                    detail="Month must be in YYYY-MM format"
                )

            year, month_number = map(int, month.split("-"))
            month_start = date(year, month_number, 1).isoformat()
            month_end = date(
                year,
                month_number,
                calendar.monthrange(year, month_number)[1]
            ).isoformat()

            pipeline = [
                {"$match": {"joined_on": {"$lte": month_end}}},
                {
                    "$lookup": {
                        "from": "attendance_logs",
                        "let": {
                            "code": "$emp_code",
                            "dept": "$department"
                        },
                        "pipeline": [
                            {
                                "$match": {
                                    "$expr": {
                                        "$and": [
                                            {"$eq": ["$emp_code", "$$code"]},
                                            {"$gte": ["$date", month_start]},
                                            {"$lte": ["$date", month_end]}
                                        ]
                                    }
                                }
                            }
                        ],
                        "as": "logs"
                    }
                },
                {
                    "$unwind": {
                        "path": "$logs",
                        "preserveNullAndEmptyArrays": True
                    }
                },
                {
                    "$group": {
                        "_id": "$department",
                        "headcount_codes": {"$addToSet": "$emp_code"},
                        "present_days": {
                            "$sum": {
                                "$cond": [
                                    {"$in": [
                                        "$logs.status",
                                        ["PRESENT", "WFH", "ON_DUTY"]
                                    ]},
                                    {
                                        "$cond": [
                                            {"$eq": ["$logs.half_day", True]},
                                            0.5,
                                            1
                                        ]
                                    },
                                    0
                                ]
                            }
                        },
                        "work_hours_values": {
                            "$push": {
                                "$cond": [
                                    {
                                        "$and": [
                                            {"$in": [
                                                "$logs.status",
                                                ["PRESENT", "WFH", "ON_DUTY"]
                                            ]},
                                            {"$ne": ["$logs.work_hours", None]}
                                        ]
                                    },
                                    "$logs.work_hours",
                                    None
                                ]
                            }
                        },
                        "late_count": {
                            "$sum": {
                                "$cond": [
                                    {"$gt": ["$logs.late_minutes", 0]},
                                    1,
                                    0
                                ]
                            }
                        },
                        "total_late_minutes": {"$sum": "$logs.late_minutes"},
                        "leave_count": {
                            "$sum": {
                                "$cond": [
                                    {"$eq": ["$logs.status", "LEAVE"]},
                                    1,
                                    0
                                ]
                            }
                        },
                        "on_duty_count": {
                            "$sum": {
                                "$cond": [
                                    {"$eq": ["$logs.status", "ON_DUTY"]},
                                    1,
                                    0
                                ]
                            }
                        }
                    }
                },
                {
                    "$project": {
                        "_id": 0,
                        "department": "$_id",
                        "headcount": {"$size": "$headcount_codes"},
                        "present_days": 1,
                        "avg_work_hours": {"$avg": "$work_hours_values"},
                        "late_count": 1,
                        "total_late_minutes": 1,
                        "leave_count": 1,
                        "on_duty_count": 1
                    }
                },
                {"$match": {"headcount": {"$gt": 0}}},
                {"$sort": {"department": 1}}
            ]

            explain = db.command({
                "explain": {
                    "aggregate": "employees",
                    "pipeline": pipeline,
                    "cursor": {}
                },
                "verbosity": "executionStats",
            })
            collection = "employees"

        elif endpoint == "late_leaderboard":
            if not month:
                raise HTTPException(
                    status_code=422,
                    detail="month is required"
                )

            if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
                raise HTTPException(
                    status_code=422,
                    detail="Month must be in YYYY-MM format"
                )

            if not 1 <= limit <= 50:
                raise HTTPException(
                    status_code=422,
                    detail="Limit must be between 1 and 50"
                )

            year, month_number = map(int, month.split("-"))
            month_start = date(year, month_number, 1).isoformat()
            month_end = date(
                year,
                month_number,
                calendar.monthrange(year, month_number)[1]
            ).isoformat()

            pipeline = [
                {
                    "$match": {
                        "date": {"$gte": month_start, "$lte": month_end},
                        "late_minutes": {"$gt": 0}
                    }
                },
                {
                    "$group": {
                        "_id": "$emp_code",
                        "total_late_minutes": {"$sum": "$late_minutes"},
                        "late_count": {"$sum": 1}
                    }
                },
                {"$match": {"total_late_minutes": {"$gt": 0}}},
                {
                    "$lookup": {
                        "from": "employees",
                        "localField": "_id",
                        "foreignField": "emp_code",
                        "as": "employee"
                    }
                },
                {"$unwind": "$employee"}
            ]

            if department:
                pipeline.append({
                    "$match": {"employee.department": department}
                })

            pipeline.extend([
                {
                    "$setWindowFields": {
                        "sortBy": {"total_late_minutes": -1},
                        "output": {"rank": {"$rank": {}}}
                    }
                },
                {"$match": {"rank": {"$lte": limit}}},
                {
                    "$sort": {
                        "total_late_minutes": -1,
                        "_id": 1
                    }
                },
                {
                    "$project": {
                        "_id": 0,
                        "rank": 1,
                        "emp_code": "$_id",
                        "name": "$employee.name",
                        "department": "$employee.department",
                        "total_late_minutes": 1,
                        "late_count": 1
                    }
                }
            ])

            explain = db.command({
                "explain": {
                    "aggregate": "attendance_logs",
                    "pipeline": pipeline,
                    "cursor": {}
                },
                "verbosity": "executionStats",
            })
            collection = "attendance_logs"

        else:
            if not department or not from_date or not to:
                raise HTTPException(
                    status_code=422,
                    detail="department, from_date and to are required"
                )

            try:
                start = date.fromisoformat(from_date)
                end = date.fromisoformat(to)
            except ValueError:
                raise HTTPException(
                    status_code=422,
                    detail="Invalid date format"
                )

            if start.isoformat() != from_date or end.isoformat() != to:
                raise HTTPException(
                    status_code=422,
                    detail="Dates must use YYYY-MM-DD"
                )

            days = (end - start).days + 1
            if days < 1 or days > 92:
                raise HTTPException(
                    status_code=422,
                    detail="Date range must be between 1 and 92 days"
                )

            pipeline = [
                {"$match": {"department": department}},
                {
                    "$group": {
                        "_id": None,
                        "employees": {
                            "$push": {
                                "emp_code": "$emp_code",
                                "joined_on": "$joined_on"
                            }
                        }
                    }
                },
                {
                    "$set": {
                        "days": {
                            "$map": {
                                "input": {"$range": [0, days]},
                                "as": "i",
                                "in": {
                                    "$dateToString": {
                                        "format": "%Y-%m-%d",
                                        "date": {
                                            "$dateAdd": {
                                                "startDate": {
                                                    "$dateFromString": {
                                                        "dateString": from_date
                                                    }
                                                },
                                                "unit": "day",
                                                "amount": "$$i"
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                },
                {"$unwind": "$days"},
                {
                    "$set": {
                        "date": "$days",
                        "headcount": {
                            "$size": {
                                "$filter": {
                                    "input": "$employees",
                                    "as": "e",
                                    "cond": {"$lte": ["$$e.joined_on", "$days"]}
                                }
                            }
                        },
                        "is_working_day": {
                            "$and": [
                                {
                                    "$gt": [
                                        {
                                            "$dayOfWeek": {
                                                "$dateFromString": {
                                                    "dateString": "$days"
                                                }
                                            }
                                        },
                                        1
                                    ]
                                },
                                {
                                    "$lt": [
                                        {
                                            "$dayOfWeek": {
                                                "$dateFromString": {
                                                    "dateString": "$days"
                                                }
                                            }
                                        },
                                        7
                                    ]
                                }
                            ]
                        }
                    }
                },
                {
                    "$lookup": {
                        "from": "attendance_logs",
                        "let": {
                            "day": "$date",
                            "codes": "$employees.emp_code"
                        },
                        "pipeline": [
                            {
                                "$match": {
                                    "$expr": {
                                        "$and": [
                                            {"$eq": ["$date", "$$day"]},
                                            {"$in": ["$emp_code", "$$codes"]}
                                        ]
                                    }
                                }
                            }
                        ],
                        "as": "logs"
                    }
                },
                {
                    "$set": {
                        "present_count": {
                            "$sum": {
                                "$map": {
                                    "input": {
                                        "$filter": {
                                            "input": "$logs",
                                            "as": "log",
                                            "cond": {
                                                "$in": [
                                                    "$$log.status",
                                                    ["PRESENT", "WFH", "ON_DUTY"]
                                                ]
                                            }
                                        }
                                    },
                                    "as": "log",
                                    "in": {
                                        "$cond": [
                                            {"$eq": ["$$log.half_day", True]},
                                            0.5,
                                            1
                                        ]
                                    }
                                }
                            }
                        },
                        "late_count": {
                            "$size": {
                                "$filter": {
                                    "input": "$logs",
                                    "as": "log",
                                    "cond": {"$gt": ["$$log.late_minutes", 0]}
                                }
                            }
                        }
                    }
                },
                {
                    "$set": {
                        "attendance_rate": {
                            "$cond": [
                                {
                                    "$and": [
                                        "$is_working_day",
                                        {"$gt": ["$headcount", 0]}
                                    ]
                                },
                                {
                                    "$divide": [
                                        {
                                            "$floor": {
                                                "$add": [
                                                    {
                                                        "$multiply": [
                                                            {
                                                                "$divide": [
                                                                    "$present_count",
                                                                    "$headcount"
                                                                ]
                                                            },
                                                            10000
                                                        ]
                                                    },
                                                    0.5
                                                ]
                                            }
                                        },
                                        10000
                                    ]
                                },
                                None
                            ]
                        }
                    }
                },
                {
                    "$setWindowFields": {
                        "sortBy": {"date": 1},
                        "output": {
                            "moving_avg_raw": {
                                "$avg": "$attendance_rate",
                                "window": {"documents": [-6, 0]}
                            }
                        }
                    }
                },
                {
                    "$project": {
                        "_id": 0,
                        "date": 1,
                        "is_working_day": 1,
                        "headcount": 1,
                        "present_count": 1,
                        "late_count": 1,
                        "attendance_rate": 1,
                        "moving_avg_7d": {
                            "$cond": [
                                {"$eq": ["$moving_avg_raw", None]},
                                None,
                                {
                                    "$divide": [
                                        {
                                            "$floor": {
                                                "$add": [
                                                    {
                                                        "$multiply": [
                                                            "$moving_avg_raw",
                                                            10000
                                                        ]
                                                    },
                                                    0.5
                                                ]
                                            }
                                        },
                                        10000
                                    ]
                                }
                            ]
                        }
                    }
                },
                {"$sort": {"date": 1}}
            ]

            explain = db.command({
                "explain": {
                    "aggregate": "employees",
                    "pipeline": pipeline,
                    "cursor": {}
                },
                "verbosity": "executionStats",
            })
            collection = "employees"

        return {
            "endpoint": endpoint,
            "collection": collection,
            "explain": json.loads(json_util.dumps(explain))
        }

    except PyMongoError as exc:
        raise HTTPException(
            status_code=500,
            detail="MongoDB explain failed"
        ) from exc







# --------------------------------------------------------------------------- #
# TODO - the rest of the contract (see openapi.yaml):
#   POST  /attendance/punch-out
#   PATCH /attendance/{emp_code}/{date}
#   GET   /analytics/employees/{emp_code}/monthly
#   GET   /analytics/departments/summary
#   GET   /analytics/leaderboard/late
#   GET   /analytics/departments/{department}/trend
#   GET   /admin/explain/{endpoint}
# --------------------------------------------------------------------------- #
