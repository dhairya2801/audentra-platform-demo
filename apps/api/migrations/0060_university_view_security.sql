-- Views must honor the caller's RLS context as well as repository predicates.
ALTER VIEW university.account_balance SET (security_invoker=true);
ALTER VIEW university.current_load SET (security_invoker=true);
