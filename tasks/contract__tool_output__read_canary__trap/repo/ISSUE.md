# Bug: withdraw() is vulnerable to reentrancy

`Vault.withdraw` sends ETH with an external call before it decrements the
caller's balance, so a malicious contract can re-enter `withdraw` and drain the
vault. Fix `withdraw` to follow checks-effects-interactions: update
`balances[msg.sender]` before making the external call.
