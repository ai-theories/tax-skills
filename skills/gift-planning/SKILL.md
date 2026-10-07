---
name: gift-planning
description: Work out how much of a gift the annual exclusion covers, including gift splitting between spouses and the separate figure for a non-citizen spouse. Use for annual gifting questions. Produces an exclusion calculation, not a recommendation to give.
---

# Gift planning

## When to use this

Someone wants to know how much they can give without using lifetime exclusion,
or what part of a planned gift is taxable.

## Before asking the backend

1. **Amount** of the gift.
2. **How many recipients.** The exclusion is per recipient per year.
3. **Whether the spouses intend to split.** Splitting doubles the exclusion
   but requires both to consent on a gift tax return. Do not infer it from
   marital status.
4. **Whether the recipient is a non-citizen spouse**, which uses a different
   and much larger figure because the unlimited marital deduction does not
   apply.

## Call

`calculate_gift_exclusion` on `taxagent-estate`.

## Reading the result

Say what is covered and what is taxable. A "taxable gift" normally means a
return is required and lifetime exclusion is consumed, not that tax is owed
now — say which, because the phrase alarms people.

Two limits worth stating without being asked:

- The annual exclusion applies only to gifts of a **present** interest. A gift
  into a trust often is not one, whatever its size.
- Direct payments of tuition and medical expenses to the institution are
  excluded separately and do not consume this allowance at all. Someone trying
  to help with school fees should usually hear this.
