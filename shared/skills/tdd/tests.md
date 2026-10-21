# Good and Bad Tests

## Good Tests

**Integration-style**: Test through real interfaces, not mocks of internal parts.

```typescript
// GOOD: Tests observable behavior
test("user can checkout with valid cart", async () => {
  const cart = createCart();
  cart.add(product);
  const result = await checkout(cart, paymentMethod);
  expect(result.status).toBe("confirmed");
});
```

Characteristics:

- Tests behavior users/callers care about
- Uses public API only
- Survives internal refactors
- Describes WHAT, not HOW
- One logical assertion per test

## Bad Tests

**Implementation-detail tests**: Coupled to internal structure.

```typescript
// BAD: Tests implementation details
test("checkout calls paymentService.process", async () => {
  const mockPayment = jest.mock(paymentService);
  await checkout(cart, payment);
  expect(mockPayment.process).toHaveBeenCalledWith(cart.total);
});
```

Red flags:

- Mocking internal collaborators
- Testing private methods
- Asserting on call counts/order
- Test breaks when refactoring without behavior change
- Test name describes HOW not WHAT
- Verifying through external means instead of interface

```typescript
// BAD: Bypasses interface to verify
test("createUser saves to database", async () => {
  await createUser({ name: "Alice" });
  const row = await db.query("SELECT * FROM users WHERE name = ?", ["Alice"]);
  expect(row).toBeDefined();
});

// GOOD: Verifies through interface
test("createUser makes user retrievable", async () => {
  const user = await createUser({ name: "Alice" });
  const retrieved = await getUser(user.id);
  expect(retrieved.name).toBe("Alice");
});
```

**Tautological tests**: Expected value restates the implementation, so the test passes by construction.

```typescript
// BAD: Expected value is recomputed the way the code computes it
test("calculateTotal sums line items", () => {
  const items = [{ price: 10 }, { price: 5 }];
  const expected = items.reduce((sum, i) => sum + i.price, 0);
  expect(calculateTotal(items)).toBe(expected);
});

// GOOD: Expected value is an independent, known literal
test("calculateTotal sums line items", () => {
  expect(calculateTotal([{ price: 10 }, { price: 5 }])).toBe(15);
});
```

**Mirror tests**: The expectations copy the code's own table, so the test changes in lock-step with the code.

```typescript
// BAD: Restates the rank table the code holds
test("statusRank ranks each status", () => {
  expect(statusRank("open")).toBe(0);
  expect(statusRank("done")).toBe(1);
  expect(statusRank("blocked")).toBe(2);
});

// GOOD: Tests the behaviour that consumes the table
test("blocked tasks sort after open tasks", () => {
  const sorted = sortTasks([blockedTask, openTask]);
  expect(sorted.map((t) => t.id)).toEqual(["open-1", "blocked-1"]);
});
```

**Contract tested twice**: The parser is a shared seam, and its tests already prove the edge cases.

```typescript
// BAD: The command test repeats the parser's edge cases
test.each(["", "  ", "1.", "1..2"])("run rejects bad id %s", async (id) => {
  await expect(runCommand(["show", id])).rejects.toThrow("bad task id");
});

// GOOD: The command test checks the composition: one valid input, one invalid input
test("run shows the task for a valid id", async () => {
  expect(await runCommand(["show", "1.2"])).toContain("Task 1.2");
});

test("run reports a bad id", async () => {
  await expect(runCommand(["show", "1..2"])).rejects.toThrow("bad task id");
});
```

**E2E re-testing component detail**: The form's component tests already prove each validation message.

```typescript
// BAD: The E2E test re-checks a validation message
test("signup rejects a short password", async ({ page }) => {
  await page.goto("/signup");
  await page.fill("#password", "abc");
  await page.click("text=Sign up");
  await expect(page.getByText("At least 8 characters")).toBeVisible();
});

// GOOD: The E2E test covers the critical path across components
test("a new user signs up and lands on the dashboard", async ({ page }) => {
  await page.goto("/signup");
  await page.fill("#email", "ada@example.com");
  await page.fill("#password", "correct-horse");
  await page.click("text=Sign up");
  await expect(page).toHaveURL("/dashboard");
});
```
