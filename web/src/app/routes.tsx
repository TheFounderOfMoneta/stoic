import { createBrowserRouter, Navigate } from "react-router";
import { Layout } from "./components/Layout";
import { HomePage } from "./pages/HomePage";
import { AssistantPage } from "./pages/AssistantPage";
import { MessagesPage } from "./pages/MessagesPage";
import { KnowledgePage } from "./pages/KnowledgePage";
import { ToolsPage } from "./pages/ToolsPage";
import { SettingsPage } from "./pages/SettingsPage";

export const router = createBrowserRouter([
  {
    path: "/",
    Component: Layout,
    children: [
      { index: true, element: <Navigate to="/home" replace /> },
      { path: "home", Component: HomePage },
      { path: "assistant", Component: AssistantPage },
      { path: "messages", Component: MessagesPage },
      { path: "knowledge", Component: KnowledgePage },
      { path: "tools", Component: ToolsPage },
      { path: "settings", Component: SettingsPage },
      { path: "*", element: <Navigate to="/home" replace /> },
    ],
  },
]);
