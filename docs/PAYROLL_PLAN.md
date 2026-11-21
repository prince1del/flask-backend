# NEXORA Payroll — plan (owner note B4: "INDPayroll ki tarah complete payroll")

_Written 2026-11-21. Nothing here is built yet; it waits for the owner's yes and order._

## 1. What NEXORA already has (Salary, Business + Distributor UI)

- **Staff**: name, phone, designation, monthly / daily pay, joining date, bank / UPI, optional NEXORA login.
- **Monthly salary sheet**: present days (from Team duty attendance or typed), earned, bonus, deductions, advance
  recovered, net; **Pay** saves an expense "Salary" from cash / bank (shows in Cash & Bank, Day book, P&L).
- **Advances** and their recovery from later salary.
- **PF / ESI / Professional tax / TDS (new regime)** working per payment, rates on the server (`hop_payroll_tax.py`),
  employer share as firm cost, **Pay dues** to the government.
- ✨ Checks: paid twice, net below zero, big jump, advance bigger than a month's pay.

## 2. What a full payroll (INDPayroll-like) needs — gaps

| # | Part | What it means | Size |
|---|---|---|---|
| 1 | **Salary structure** | Per staff: Basic, HRA, Conveyance, Special allowance, other earnings; CTC → monthly breakup; templates per designation | M |
| 2 | **Attendance & leave** | Leave types (CL / SL / EL / LOP), yearly quota, carry forward, holidays list, half day, late marks, overtime hours → pay | L |
| 3 | **Payslip** | PDF payslip per staff per month (firm brand), share on WhatsApp / email, staff sees own payslips in their login | M |
| 4 | **Bank payout** | Bulk bank transfer file (bank's CSV format) for all salaries; mark paid | S |
| 5 | **Statutory files** | EPFO **ECR** text file, ESIC monthly contribution sheet, PT challan summary, **Form 16** (Part B), **24Q** quarterly TDS data | L |
| 6 | **Loans to staff** | Loan with EMI cut every month, balance, interest optional | S |
| 7 | **Arrears & revisions** | Salary revision from a back date → arrears in the next month | M |
| 8 | **Full & final** | Leaving staff: pending salary, leave encashment, notice pay, gratuity, recoveries → F&F statement | M |
| 9 | **Bonus & gratuity** | Payment of Bonus Act (8.33–20%) yearly working; gratuity provision (15/26 × last basic × years) | S |
| 10 | **Reports** | Salary register, PF / ESI register, leave register, cost by department / UI | S |
| 11 | **AI** | ✨ "Is mahine salary kitni jayegi", wrong PF / ESI applicability, staff with many LOP days, rate-change watch (never auto-applied) | S |

## 3. What is needed from the owner

1. Which firm(s) are registered with **EPFO / ESIC** and the **state** for Professional tax.
2. The order: suggested **1 → 3 → 2 → 4 → 5**, then the rest.
3. A CA (or INDPayroll output) for one month to compare against — the numbers must match to the rupee.

## 4. How to test (made-up data only)

- A test firm with 5 made-up staff: one below the ESI limit, one above the PF ceiling, one daily-wage, one who
  joins mid-month, one who leaves (F&F).
- Run two months: attendance with leave and LOP, an advance, a loan EMI, a revision with arrears.
- Check every payslip, the salary register, the ECR file and the ESIC sheet against a hand / CA working; the PF and
  ESI totals must match the challans.
- AI tests stay inside the daily limit (one capped call each); nothing above ₹10 without asking.
