/**
 * Node self-check for js/checklist.js. Run: node test_checklist.js
 */
const { build } = require("./js/checklist.js");

const ALL_PASS_RECORD = {
  summary: {
    verificationRecord: {
      transactionHash: "0x" + "ab".repeat(32),
      chainId: "0x14a34",
      chainIdMatches: true,
      networkLabel: "eip155:84532",
      transactionFound: true,
      receiptStatus: "0x1",
      blockNumber: "0x2c54615",
      anchorBlock: "0x2c54615",
      transfer: {
        found: true,
        from: "0x959d38cb56dee59f22562bb8304cf7d45b6524e2",
        to: "0x959d38cb56dee59f22562bb8304cf7d45b6524e2",
        value: 1000,
      },
      authorization: {
        found: true,
        selector: "0xe3ee160e",
        hasEIP3009Selector: true,
      },
      verificationStatus: "VERIFIED",
      reason: null,
    },
    auditRecord: {
      anchorBlock: 46482965,
      authValue: 1000,
      claimPayer: "0x959d38cb56dee59f22562bb8304cf7d45b6524e2",
      claimSource: "self_probe",
      observed: [
        {
          amount: 1000,
          from: "0x959d38cb56dee59f22562bb8304cf7d45b6524e2",
          to: "0x959d38cb56dee59f22562bb8304cf7d45b6524e2",
          token: "0x036cbd53842c5426634e7929541ec2318f3dcf7e",
        },
      ],
      reqAsset: "0x036cbd53842c5426634e7929541ec2318f3dcf7e",
      reqMaxAmountRequired: "1000",
      reqPayTo: "0x959d38cb56dee59f22562bb8304cf7d45b6524e2",
      reqScheme: "exact",
      settledAmount: 1000,
    },
    requirements: {
      scheme: "exact",
      network: "eip155:84532",
      payTo: "0x959d38cb56deE59f22562Bb8304CF7d45b6524E2",
      asset: "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
      maxAmountRequired: "1000",
    },
  },
};

function run() {
  const checks = [];

  const allPass = build(ALL_PASS_RECORD);
  checks.push(["a complete record yields 9 checklist items", allPass.length === 9]);
  checks.push([
    "a complete record passes every check",
    allPass.every((i) => i.status === "pass"),
  ]);

  const mixed = build({
    summary: {
      verificationRecord: {
        transactionFound: true,
        receiptStatus: "0x0",
        chainIdMatches: false,
        transfer: {
          from: "0x1111111111111111111111111111111111111111",
          to: "0x959d38cb56dee59f22562bb8304cf7d45b6524e2",
          value: 2000,
        },
        authorization: { found: false },
      },
      auditRecord: {
        claimPayer: "0x959d38cb56dee59f22562bb8304cf7d45b6524e2",
        reqScheme: "exact",
        settledAmount: 2000,
        authValue: 1000,
      },
      requirements: {
        payTo: "0x959d38cb56dee59f22562bb8304cf7d45b6524e2",
        asset: "0x036cbd53842c5426634e7929541ec2318f3dcf7e",
        maxAmountRequired: "1000",
      },
    },
  });
  const byLabel = {};
  mixed.forEach((i) => { byLabel[i.label] = i.status; });
  checks.push(["a reverted receipt is a fail", byLabel["Receipt status"] === "fail"]);
  checks.push([
    "a chain mismatch is a fail",
    byLabel["Network label matches chain"] === "fail",
  ]);
  checks.push(["a payer mismatch is a fail", byLabel["Payer"] === "fail"]);
  checks.push([
    "a missing observed token asset is unknown",
    byLabel["Asset"] === "unknown",
  ]);
  checks.push([
    "settled above the authorized value is a fail",
    byLabel["Amount within scheme limit"] === "fail",
  ]);
  checks.push([
    "a missing authorization is a fail",
    byLabel["EIP-3009 authorization present"] === "fail",
  ]);
  checks.push([
    "a missing anchor is unknown",
    byLabel["Finality anchor recorded"] === "unknown",
  ]);

  const empty = build({ summary: {} });
  checks.push([
    "an empty record yields 9 unknown items and never throws",
    empty.length === 9 && empty.every((i) => i.status === "unknown"),
  ]);

  let failures = 0;
  checks.forEach(([description, passed]) => {
    console.log(description + ": " + (passed ? "True" : "FAILED"));
    if (!passed) failures += 1;
  });
  console.log("");
  if (failures === 0) {
    console.log("All " + checks.length + " checks passed.");
  } else {
    console.log(String(failures) + " of " + checks.length + " checks FAILED.");
    process.exitCode = 1;
  }
}

run();
