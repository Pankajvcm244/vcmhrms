# Agency Attendance Guide



This guide explains how biometric punches become Agency Attendance, why attendance may remain pending,

how final status is calculated, and when a manager must review a record.



## Automatic attendance calculation



Attendance calculation is automatic when the third-party biometric service sends punches through the

API. Under normal operation, nobody needs to press **Recalculate Attendance**.



1. The API stores each original biometric punch in **Agency Punch**.

2. A background job recalculates the applicable **Agency Attendance** records.

3. The earliest eligible punch becomes inferred IN and the latest becomes inferred OUT.

4. The system calculates worked hours, late entry, early exit, and before/after-shift OT.

5. After the complete capture window closes, status is finalized.



**Recalculate Attendance** is a backup for testing, delayed data, roster or shift changes, manager

corrections, and troubleshooting.



## Finalization after the capture window

The biometric provider does not send a synchronization watermark. It sends only Staff ID, Biometric
Code, and Punch Time. This keeps integration simple and allows an older punch to arrive later.

ERPNext finalizes a duty when the current time passes its complete capture window. The window includes
the scheduled shift, early/late matching allowance, and configured before/after-shift OT capture hours.
The scheduler runs at 04:00 for eligible day/evening duties and at 16:00 for night duties that end in
the morning, including their after-shift capture window. These runs also finalize Absent days with no
punches. Managers can run the same calculation immediately from **Agency Attendance Review > Process Attendance**.

### Example

- Roster date: **01-10-2026**
- Shift: **14:00–22:00**
- Maximum after-shift OT capture: **6 hours**
- Additional late matching allowance: **30 minutes**
- Capture window closes: **02-10-2026 04:30**

Attendance stays **PD — Pending** until 04:30. The 16:00 job automatically finalizes it
as Present, Half Day, Absent, or Needs Review.

If an old punch arrives later, the API accepts it and queues recalculation. Attendance may change from
Absent or Half Day to the result supported by the delayed punch. Any affected approval is reset for
manager review. Original punches and changes remain auditable.

### Provider payload

```json
{
  "punches": [
    {
      "biometric_code": "900001",
      "punch_time": "2026-10-01 23:04:00"
    }
  ]
}
```

ERPNext resolves the Agency Worker from the unique Biometric Code and creates the event ID internally.
The vendor does not send Staff ID. Repeating the same punch is safe and is treated as a duplicate.

Punch times without a UTC offset are interpreted in the ERPNext site's configured timezone
(`Asia/Kolkata`). Offset-aware ISO timestamps remain supported for compatibility.

## Monthly attendance codes



| Code | Meaning | What happens next |

|---|---|---|

| `PD` | Pending | Waiting for the configured capture window to close; normally resolves automatically. |

| `NR` | Needs Review | Punches cannot be interpreted reliably; manager review is required. |

| `P` | Present | Required working hours were completed. |

| `HD` | Half Day | At least half, but less than the required hours were completed. |

| `A` | Absent | Less than half the required hours were completed, or no punches arrived before the capture window closed. |

| `OFF` | Weekly Off | The roster explicitly marks the date as weekly off. |



Additional markers:



| Marker | Meaning |

|---|---|

| `L` | Late Entry |

| `E` | Early Exit |

| `!` | Punch exception approval is pending |

| `OT?` | OT approval is pending |

| `OT 2` | Two OT hours have been approved |



## PD versus NR



**PD is a timing state.** Punch data may be correct, but the configured capture window is still open.

The next scheduled 04:00 or 16:00 run finalizes it after the window closes. A manager can use **Process Attendance**
when the result is needed before the next scheduled run.



**NR is a data interpretation problem.** Common causes are:



- Only one eligible punch exists, so an IN/OUT pair cannot be formed.

- A punch falls into overlapping duty windows.

- Multiple duties have ambiguous matching boundaries.

- A valid chronological first/last pair cannot be produced.



NR does not necessarily mean the worker made a mistake. It means the available roster and punch data

cannot produce a reliable result without review.



## Punch matching and overnight duties



Punches do not need an IN or OUT direction. Inside each duty's capture window:



- Earliest eligible punch: inferred IN

- Latest eligible punch: inferred OUT

- Duplicate timestamps: counted once during calculation



An overnight attendance belongs to the roster date on which duty started. A 22:00â€“06:00 duty starting

on 01-10-2026 belongs to the 01-10-2026 roster although OUT occurs on 02-10-2026.



## Worked hours and status



```text

Worked Hours = Last Punch - First Punch - Configured Break Minutes

```



After the capture window closes:



- Required hours or more: **Present**

- Half the required hours up to less than required hours: **Half Day**

- Less than half the required hours: **Absent**

- No punches: **Absent**

- One punch or ambiguous punches: **Needs Review**



For an eight-hour requirement:



| Worked hours | Result |

|---|---|

| 8 hours or more | Present |

| 4 hours to less than 8 hours | Half Day |

| Less than 4 hours | Absent |



## Late entry and early exit



Each Agency Shift defines separate grace periods, normally 15 minutes.



- First punch later than start plus grace: Late Entry

- Last punch earlier than end minus grace: Early Exit

- A punch exactly at the grace boundary is not flagged



Late and early flags do not independently decide Present, Half Day, or Absent. Final status is based on

worked hours, while the flags are shown separately for manager review.



## Overtime



```text

Before OT = Scheduled Start - First Punch

After OT  = Last Punch - Scheduled End

```



The roster can plan before/after-shift OT. Actual OT is captured only inside the shift's maximum OT

windows. Detected OT is not automatically payable; the assigned Supervisor or backup System Manager

must approve it. A duty with no planned or actual OT is **Not Required** and is excluded from the OT

approval queue.



## Manager review and correction



**Agency Attendance Review** contains finalized Late Entry, Early Exit, Half Day, Absent, missing-punch,

and ambiguous-punch exceptions. The assigned Supervisor can:



- Approve the calculated exception as-is.

- Enter corrected first and last punches with a mandatory reason.

- Override status with a mandatory reason.



Corrections never edit or delete original Agency Punch records. Corrected values, manager login,

timestamp, and reason are retained for audit. System Manager is the backup approver.



## Simulator behavior



The simulator is for staging and manual testing. Its punches are real immutable Agency Punch records

marked as simulated.



Historical test attendance finalizes automatically after its capture window closes. For an immediate

result, use a sufficiently old test date or click **Recalculate Attendance** after the closing time.



## When attendance remains PD



1. Confirm roster and shift times.

2. Check before/after OT capture hours and early/late matching minutes.

3. Determine when the complete capture window closes.

4. Confirm that the current time is later than the closing time.

5. Confirm that the scheduler and background workers are running.

6. Recalculate after correcting a roster or shift.



## When attendance shows NR



1. Confirm at least two eligible punches exist.

2. Check whether punches are inside the duty and OT window.

3. Check overlapping or multiple duties.

4. Read the exception on Agency Attendance Review.

5. Correct the punch pair or apply a justified status exception.




