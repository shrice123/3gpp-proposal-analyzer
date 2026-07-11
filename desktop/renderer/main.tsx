import React from "react";
import { createRoot } from "react-dom/client";
import { ProposalApp } from "@app/proposal-app";
import "@app/globals.css";

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ProposalApp />
  </React.StrictMode>,
);

