// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "forge-std/Test.sol";
import "../src/Vault.sol";

contract VaultTest is Test {
    Vault vault;

    function setUp() public {
        vault = new Vault();
    }

    function testDepositThenWithdraw() public {
        vault.deposit{value: 1 ether}();
        vault.withdraw(1 ether);
        assertEq(vault.balances(address(this)), 0);
    }

    receive() external payable {}
}
