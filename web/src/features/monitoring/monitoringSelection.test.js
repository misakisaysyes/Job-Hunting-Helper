import test from "node:test";
import assert from "node:assert/strict";
import { monitoringKey, selectPageConversations, toggleSelectedConversation } from "./monitoringSelection.js";

test("exchange selections survive page changes and page select only changes the visible page", () => {
  const firstPage = [
    { platform: "boss", conversation_id: "1" },
    { platform: "boss", conversation_id: "2" },
  ];
  const secondPage = [
    { platform: "boss", conversation_id: "3" },
    { platform: "boss", conversation_id: "4" },
  ];
  const firstSelected = toggleSelectedConversation(new Map(), firstPage[0]);
  const acrossPages = selectPageConversations(firstSelected, secondPage, true);
  assert.deepEqual([...acrossPages.keys()], ["boss:1", "boss:3", "boss:4"]);
  assert.deepEqual([...firstSelected.keys()], ["boss:1"]);

  const withoutSecondPage = selectPageConversations(acrossPages, secondPage, false);
  assert.deepEqual([...withoutSecondPage.keys()], [monitoringKey(firstPage[0])]);
});
