from dataclasses import dataclass, field
from collections import defaultdict


@dataclass
class Expense:
    description: str
    amount: float
    paid_by: str
    split_among: list[str]

    def share(self) -> float:
        return self.amount / len(self.split_among)


@dataclass
class Trip:
    name: str
    members: list[str] = field(default_factory=list)
    expenses: list[Expense] = field(default_factory=list)

    def add_member(self, name: str):
        if name in self.members:
            print(f"  {name} is already on the trip.")
        else:
            self.members.append(name)
            print(f"  Added {name} to the trip.")

    def add_expense(self, description: str, amount: float, paid_by: str, split_among: list[str]):
        if paid_by not in self.members:
            print(f"  Error: {paid_by} is not on the trip.")
            return
        invalid = [p for p in split_among if p not in self.members]
        if invalid:
            print(f"  Error: {', '.join(invalid)} are not on the trip.")
            return
        expense = Expense(description, amount, paid_by, split_among)
        self.expenses.append(expense)
        print(f"  Added expense: {description} (${amount:.2f}) paid by {paid_by}, split among {', '.join(split_among)}.")

    def balances(self) -> dict[str, float]:
        """Returns net balance per person. Positive = owed money. Negative = owes money."""
        bal = defaultdict(float)
        for exp in self.expenses:
            bal[exp.paid_by] += exp.amount
            share = exp.share()
            for person in exp.split_among:
                bal[person] -= share
        return dict(bal)

    def settlements(self) -> list[tuple[str, str, float]]:
        """Returns a minimal list of (debtor, creditor, amount) to settle all debts."""
        bal = self.balances()
        debtors = sorted([(p, -b) for p, b in bal.items() if b < -0.005], key=lambda x: x[1], reverse=True)
        creditors = sorted([(p, b) for p, b in bal.items() if b > 0.005], key=lambda x: x[1], reverse=True)
        debtors = [[p, amt] for p, amt in debtors]
        creditors = [[p, amt] for p, amt in creditors]

        result = []
        i, j = 0, 0
        while i < len(debtors) and j < len(creditors):
            debtor, owe = debtors[i]
            creditor, get = creditors[j]
            transfer = min(owe, get)
            result.append((debtor, creditor, round(transfer, 2)))
            debtors[i][1] -= transfer
            creditors[j][1] -= transfer
            if debtors[i][1] < 0.005:
                i += 1
            if creditors[j][1] < 0.005:
                j += 1
        return result

    def summary(self):
        print(f"\n{'='*50}")
        print(f"  Trip: {self.name}")
        print(f"  Members: {', '.join(self.members)}")
        print(f"\n  Expenses:")
        if not self.expenses:
            print("    None yet.")
        for exp in self.expenses:
            print(f"    - {exp.description}: ${exp.amount:.2f} paid by {exp.paid_by}, split among {', '.join(exp.split_among)}")

        print(f"\n  Net Balances:")
        for person, bal in self.balances().items():
            if bal > 0.005:
                print(f"    {person} is owed ${bal:.2f}")
            elif bal < -0.005:
                print(f"    {person} owes ${abs(bal):.2f}")
            else:
                print(f"    {person} is settled up")

        print(f"\n  Settlements (who pays who):")
        settlements = self.settlements()
        if not settlements:
            print("    Everyone is settled up!")
        for debtor, creditor, amount in settlements:
            print(f"    {debtor} -> {creditor}: ${amount:.2f}")
        print(f"{'='*50}\n")


# ── CLI helpers ────────────────────────────────────────────────────────────────

def pick_members(members: list[str], prompt: str = "Choose members") -> list[str]:
    print(f"  {prompt}:")
    for i, m in enumerate(members, 1):
        print(f"    {i}. {m}")
    print(f"    0. Everyone")
    raw = input("  Enter numbers separated by commas (or 0 for all): ").strip()
    if raw == "0":
        return list(members)
    chosen = []
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit() and 1 <= int(part) <= len(members):
            chosen.append(members[int(part) - 1])
        else:
            print(f"  Skipping invalid choice: {part!r}")
    return chosen


def pick_one_member(members: list[str], prompt: str) -> str | None:
    print(f"  {prompt}:")
    for i, m in enumerate(members, 1):
        print(f"    {i}. {m}")
    raw = input("  Enter number: ").strip()
    if raw.isdigit() and 1 <= int(raw) <= len(members):
        return members[int(raw) - 1]
    print("  Invalid choice.")
    return None


def main():
    print("\n=== Trip Expense Splitter ===\n")
    trip_name = input("Enter trip name: ").strip() or "My Trip"
    trip = Trip(name=trip_name)

    while True:
        print("\nWhat would you like to do?")
        print("  1. Add member")
        print("  2. Add expense")
        print("  3. View summary")
        print("  4. Quit")
        choice = input("Choice: ").strip()

        if choice == "1":
            name = input("  Member name: ").strip()
            if name:
                trip.add_member(name)

        elif choice == "2":
            if len(trip.members) < 2:
                print("  Add at least 2 members before adding expenses.")
                continue
            description = input("  Description (e.g. Groceries): ").strip()
            if not description:
                continue
            try:
                amount = float(input("  Amount ($): ").strip())
            except ValueError:
                print("  Invalid amount.")
                continue
            paid_by = pick_one_member(trip.members, "Who paid?")
            if not paid_by:
                continue
            split_among = pick_members(trip.members, "Split among who?")
            if not split_among:
                print("  Must select at least one person.")
                continue
            trip.add_expense(description, amount, paid_by, split_among)

        elif choice == "3":
            trip.summary()

        elif choice == "4":
            trip.summary()
            print("Goodbye!")
            break

        else:
            print("  Invalid choice, try again.")


if __name__ == "__main__":
    main()
