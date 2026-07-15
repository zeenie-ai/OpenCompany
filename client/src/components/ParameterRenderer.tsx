/* eslint-disable no-case-declarations -- legacy switch-renderer; case bodies use const/let intentionally. */
/* eslint-disable react-hooks/rules-of-hooks -- legacy switch-renderer uses internal helpers with hooks; pre-existing structural pattern. */
/* eslint-disable react-hooks/exhaustive-deps -- legacy effects with intentionally-omitted deps (parameter / selectedNode reads stay stable across the panel's lifetime). */
import React, { useState, useEffect } from 'react';
import { NodeParameter } from '../types/NodeTypes';
import { INodeProperties, INodePropertyOption } from '../types/INodeProperties';
import APIKeyValidator from './APIKeyValidator';
import CodeEditor from './ui/CodeEditor';
import DynamicParameterService from '../services/dynamicParameterService';
import { useAppStore } from '../store/useAppStore';
import { isNodeInBackendGroup } from '../lib/nodeSpec';
import { API_CONFIG } from '../config/api';
import { useWebSocket } from '../contexts/WebSocketContext';
import { useApiKeys } from '../hooks/useApiKeys';
import { Input as ShadcnInput } from './ui/input';
import { Textarea } from './ui/textarea';
import { Checkbox } from './ui/checkbox';
import { Slider } from './ui/slider';
import { Alert } from './ui/alert';
import { ActionButton } from './ui/action-button';
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectTrigger,
  SelectValue,
} from './ui/select';
import { shouldShowParameter } from '../utils/parameterVisibility';

// Map node types to provider keys for AI model nodes
import { AI_MODEL_PROVIDER_MAP } from '../lib/aiModelProviders';

import { resolveNodeDescription } from '../lib/nodeSpec';
// Map node types to provider keys for AI model nodes
// Uses the centralized map from aiModelProviders.ts. Local-server
// providers (ollama, lmstudio) live in that map alongside the cloud
// providers, so no per-type override is needed here. The remaining
// aliases below cover renamed-but-still-saved-in-old-workflows types.
const NODE_TYPE_TO_PROVIDER: Record<string, string> = {
  ...AI_MODEL_PROVIDER_MAP,
  // Legacy aliases for backward compatibility
  'claudeChatModel': 'anthropic',
  'googleChatModel': 'gemini',
  'azureChatModel': 'azure_openai',
  'cohereChatModel': 'cohere',
};

// Collection Renderer - n8n official style
const CollectionRenderer: React.FC<{
  parameter: any;
  value: any;
  onChange: (value: any) => void;
  allParameters?: Record<string, any>;
}> = ({ parameter, value, onChange, allParameters }) => {
  const [showAddOption, setShowAddOption] = useState(false);
  const currentValue = value || {};
  const addedOptions = Object.keys(currentValue).filter(key => currentValue[key] !== undefined);
  const availableOptions = parameter.options?.filter((opt: any) => !addedOptions.includes(opt.name)) || [];

  const addOption = (optionName: string) => {
    const option = parameter.options?.find((opt: any) => opt.name === optionName);
    if (option) {
      onChange({
        ...currentValue,
        [optionName]: option.default
      });
      setShowAddOption(false);
    }
  };

  const removeOption = (optionName: string) => {
    const newValue = { ...currentValue };
    delete newValue[optionName];
    onChange(newValue);
  };

  const updateOption = (optionName: string, optionValue: any) => {
    onChange({
      ...currentValue,
      [optionName]: optionValue
    });
  };

  return (
    <div>
      {addedOptions.length === 0 && (
        <div className="text-sm text-muted-foreground mb-3 py-2">
          No properties
        </div>
      )}

      {addedOptions.map((optionName) => {
        const option = parameter.options?.find((opt: any) => opt.name === optionName);
        if (!option) return null;

        return (
          <div
            key={optionName}
            className="relative mb-4 p-3 rounded-md border border-border bg-muted"
          >
            <button
              onClick={() => removeOption(optionName)}
              className="absolute top-1.5 right-1.5 rounded-sm px-1 py-0.5 text-sm text-muted-foreground cursor-pointer hover:bg-border"
              title="Remove"
            >
              ✕
            </button>
            <ParameterRenderer
              parameter={option}
              value={currentValue[optionName]}
              onChange={(newValue) => updateOption(optionName, newValue)}
              allParameters={allParameters}
            />
          </div>
        );
      })}

      {availableOptions.length > 0 && (
        <div className="relative">
          <button
            onClick={() => setShowAddOption(!showAddOption)}
            className="flex w-full items-center justify-between rounded-md border border-border bg-muted px-3 py-2.5 text-sm text-muted-foreground cursor-pointer transition-colors hover:bg-accent"
          >
            {parameter.placeholder || 'Add Option'}
            <span
              className="transition-transform"
              style={{ transform: showAddOption ? 'rotate(180deg)' : 'rotate(0deg)' }}
            >
              ▼
            </span>
          </button>

          {showAddOption && (
            <div className="absolute left-0 right-0 top-full z-[1000] mt-0.5 max-h-[200px] overflow-y-auto rounded-md border border-border bg-background shadow-sm">
              {availableOptions.map((option: any, index: number) => (
                <button
                  key={option.name}
                  onClick={() => addOption(option.name)}
                  className={`w-full cursor-pointer px-3 py-2.5 text-left text-sm text-foreground transition-colors hover:bg-muted ${index < availableOptions.length - 1 ? 'border-b border-border' : ''}`}
                >
                  <div className="font-medium">
                    {option.displayName}
                  </div>
                  {option.description && (
                    <div className="mt-0.5 text-xs text-muted-foreground">
                      {option.description}
                    </div>
                  )}
                </button>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
};

// Group ID Selector - with Load Groups button and dropdown
const GroupIdSelector: React.FC<{
  requestKey: string;
  value: string;
  onChange: (value: string) => void;
  onNameChange?: (name: string) => void;
  storedName?: string;
  placeholder?: string;
  isDragOver: boolean;
  onDragOver: (e: React.DragEvent) => void;
  onDragLeave: (e: React.DragEvent) => void;
  onDrop: (e: React.DragEvent) => void;
}> = ({ requestKey, value, onChange, onNameChange, storedName, placeholder, isDragOver, onDragOver, onDragLeave, onDrop }) => {
  const [groups, setGroups] = useState<Array<{ jid: string; name: string; topic?: string; size?: number; is_community?: boolean }>>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [showDropdown, setShowDropdown] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Use stored name if available, otherwise local state
  const [localGroupName, setLocalGroupName] = useState<string | null>(null);
  const selectedGroupName = storedName || localGroupName;
  const { getWhatsAppGroups } = useWebSocket();
  const requestGeneration = React.useRef(0);
  const activeRequestKey = React.useRef(requestKey);
  activeRequestKey.current = requestKey;

  useEffect(() => {
    requestGeneration.current += 1;
    setGroups([]);
    setLocalGroupName(null);
    setShowDropdown(false);
    setError(null);
    setIsLoading(false);
    return () => {
      requestGeneration.current += 1;
    };
  }, [requestKey]);

  // Sync local state with stored name
  useEffect(() => {
    if (storedName) {
      setLocalGroupName(storedName);
    }
  }, [storedName]);

  const handleLoadGroups = async () => {
    const request = ++requestGeneration.current;
    const requestKeyAtStart = requestKey;
    const isCurrent = () => (
      request === requestGeneration.current
      && activeRequestKey.current === requestKeyAtStart
    );
    setIsLoading(true);
    setError(null);
    try {
      const result = await getWhatsAppGroups();
      if (!isCurrent()) return;
      console.log('[GroupIdSelector] Raw groups from API:', result.groups?.map(g => ({ name: g.name, jid: g.jid, is_community: g.is_community })));
      if (result.success && result.groups.length > 0) {
        // Filter out communities - they don't have regular chat history
        const regularGroups = result.groups.filter(g => !g.is_community);
        console.log('[GroupIdSelector] After filtering communities:', regularGroups.length, 'groups remaining');
        if (regularGroups.length === 0) {
          setError('Only communities found (no chat history available)');
          return;
        }
        setGroups(regularGroups);
        setShowDropdown(true);
        // If we already have a value, try to find its name and update storage
        if (value) {
          const matchingGroup = regularGroups.find(g => g.jid === value);
          if (matchingGroup && matchingGroup.name !== storedName) {
            setLocalGroupName(matchingGroup.name);
            onNameChange?.(matchingGroup.name);
          }
        }
      } else if (result.error) {
        setError(result.error);
      } else {
        setError('No groups found');
      }
    } catch (err: any) {
      if (isCurrent()) {
        setError(err.message || 'Failed to load groups');
      }
    } finally {
      if (isCurrent()) {
        setIsLoading(false);
      }
    }
  };

  const handleSelectGroup = (group: { jid: string; name: string }) => {
    onChange(group.jid);
    setLocalGroupName(group.name);
    onNameChange?.(group.name);
    setShowDropdown(false);
  };

  const handleInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    onChange(e.target.value);
    // Clear group name when user types manually
    setLocalGroupName(null);
    onNameChange?.('');
  };

  // Display value: show group name if selected, otherwise show JID
  const displayValue = selectedGroupName || value;
  const isGroupSelected = selectedGroupName !== null && value;
  const isTemplate = !!(value && value.includes('{{'));

  return (
    <div className="relative">
      <div className="flex items-center gap-2">
        <ShadcnInput
          type="text"
          value={displayValue}
          onChange={handleInputChange}
          placeholder={placeholder || '123456789@g.us'}
          onDragOver={onDragOver}
          onDragLeave={onDragLeave}
          onDrop={onDrop}
          className={`flex-1 ${isDragOver ? 'border-ring ring-3 ring-ring/50 bg-accent/10' : ''} ${isGroupSelected ? 'bg-muted text-success font-medium' : (isTemplate ? 'text-info font-mono' : '')}`}
        />
        <ActionButton
          intent="config"
          onClick={handleLoadGroups}
          disabled={isLoading}
          title="Load WhatsApp groups"
        >
          {isLoading ? 'Loading...' : 'Load'}
        </ActionButton>
      </div>
      {/* Show JID below when group name is displayed */}
      {isGroupSelected && (
        <div className="mt-1 font-mono text-[11px] text-muted-foreground">
          {value}
        </div>
      )}

      {error && (
        <div className="mt-1 text-xs text-destructive">
          {error}
        </div>
      )}

      {showDropdown && groups.length > 0 && (
        <div className="absolute left-0 right-0 top-full z-[1000] mt-1 max-h-[200px] overflow-y-auto rounded-md border border-border bg-background shadow-sm">
          {groups.map((group, index) => (
            <button
              key={group.jid}
              onClick={() => handleSelectGroup(group)}
              className={`w-full cursor-pointer px-3 py-2.5 text-left text-[13px] text-foreground transition-colors hover:bg-muted ${index < groups.length - 1 ? 'border-b border-border' : ''}`}
            >
              <div className="font-medium">{group.name}</div>
              <div className="mt-0.5 font-mono text-[11px] text-muted-foreground">
                {group.jid}
                {group.size && <span className="ml-2">({group.size} members)</span>}
              </div>
            </button>
          ))}
          <button
            onClick={() => setShowDropdown(false)}
            className="w-full cursor-pointer border-t border-border bg-muted px-3 py-2 text-center text-xs text-muted-foreground transition-colors hover:text-foreground"
          >
            Close
          </button>
        </div>
      )}
    </div>
  );
};

// Sender Number Selector - with Load Members button and dropdown (loads from selected group)
const SenderNumberSelector: React.FC<{
  requestKey: string;
  value: string;
  onChange: (value: string) => void;
  onNameChange?: (name: string) => void;
  storedName?: string;
  placeholder?: string;
  isDragOver: boolean;
  onDragOver: (e: React.DragEvent) => void;
  onDragLeave: (e: React.DragEvent) => void;
  onDrop: (e: React.DragEvent) => void;
  groupId: string; // The selected group to load members from
}> = ({ requestKey, value, onChange, onNameChange, storedName, placeholder, isDragOver, onDragOver, onDragLeave, onDrop, groupId }) => {
  const [members, setMembers] = useState<Array<{ phone: string; name: string; jid: string; is_admin?: boolean }>>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [showDropdown, setShowDropdown] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Use stored name if available, otherwise local state
  const [localMemberName, setLocalMemberName] = useState<string | null>(null);
  const selectedMemberName = storedName || localMemberName;
  const { getWhatsAppGroupInfo } = useWebSocket();
  const requestGeneration = React.useRef(0);
  const selectorIdentity = `${requestKey}\u0000${groupId}`;
  const activeSelectorIdentity = React.useRef(selectorIdentity);
  activeSelectorIdentity.current = selectorIdentity;

  useEffect(() => {
    requestGeneration.current += 1;
    setMembers([]);
    setLocalMemberName(null);
    setShowDropdown(false);
    setError(null);
    setIsLoading(false);
    return () => {
      requestGeneration.current += 1;
    };
  }, [requestKey, groupId]);

  // Sync local state with stored name
  useEffect(() => {
    if (storedName) {
      setLocalMemberName(storedName);
    }
  }, [storedName]);

  const handleLoadMembers = async () => {
    if (!groupId) {
      setError('Select a group first');
      return;
    }

    const request = ++requestGeneration.current;
    const identityAtStart = selectorIdentity;
    const isCurrent = () => (
      request === requestGeneration.current
      && activeSelectorIdentity.current === identityAtStart
    );
    setIsLoading(true);
    setError(null);
    try {
      const result = await getWhatsAppGroupInfo(groupId);
      if (!isCurrent()) return;
      if (result.success && result.participants && result.participants.length > 0) {
        setMembers(result.participants);
        setShowDropdown(true);
        // If we already have a value, try to find its name and update storage
        if (value) {
          const matchingMember = result.participants.find((m: any) => m.phone === value);
          if (matchingMember) {
            const name = matchingMember.name || matchingMember.phone;
            if (name !== storedName) {
              setLocalMemberName(name);
              onNameChange?.(name);
            }
          }
        }
      } else if (result.error) {
        setError(result.error);
      } else {
        setError('No members found');
      }
    } catch (err: any) {
      if (isCurrent()) {
        setError(err.message || 'Failed to load members');
      }
    } finally {
      if (isCurrent()) {
        setIsLoading(false);
      }
    }
  };

  const handleSelectMember = (member: { phone: string; name: string }) => {
    const name = member.name || member.phone;
    onChange(member.phone);
    setLocalMemberName(name);
    onNameChange?.(name);
    setShowDropdown(false);
  };

  const handleClearSelection = () => {
    onChange('');
    setLocalMemberName(null);
    onNameChange?.('');
    setShowDropdown(false);
  };

  const handleInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    onChange(e.target.value);
    // Clear member name when user types manually
    setLocalMemberName(null);
    onNameChange?.('');
  };

  // Display value: show member name if selected, otherwise show phone
  const displayValue = selectedMemberName || value;
  const isMemberSelected = selectedMemberName !== null && value;
  const isTemplate = !!(value && value.includes('{{'));

  return (
    <div className="relative">
      <div className="flex items-center gap-2">
        <ShadcnInput
          type="text"
          value={displayValue}
          onChange={handleInputChange}
          placeholder={placeholder || 'All members (leave empty)'}
          onDragOver={onDragOver}
          onDragLeave={onDragLeave}
          onDrop={onDrop}
          className={`flex-1 ${isDragOver ? 'border-ring ring-3 ring-ring/50 bg-accent/10' : ''} ${isMemberSelected ? 'bg-muted text-success font-medium' : (isTemplate ? 'text-info font-mono' : '')}`}
        />
        <ActionButton
          intent="config"
          onClick={handleLoadMembers}
          disabled={isLoading || !groupId}
          title={groupId ? "Load group members" : "Select a group first"}
        >
          {isLoading ? 'Loading...' : 'Load'}
        </ActionButton>
      </div>
      {/* Show phone below when member name is displayed */}
      {isMemberSelected && (
        <div className="mt-1 font-mono text-[11px] text-muted-foreground">
          {value}
        </div>
      )}

      {error && (
        <div className="mt-1 text-xs text-destructive">
          {error}
        </div>
      )}

      {showDropdown && members.length > 0 && (
        <div className="absolute left-0 right-0 top-full z-[1000] mt-1 max-h-[250px] overflow-y-auto rounded-md border border-border bg-background shadow-sm">
          {/* All Members option */}
          <button
            onClick={handleClearSelection}
            className={`w-full cursor-pointer border-b border-border px-3 py-2.5 text-left text-[13px] text-foreground transition-colors hover:bg-muted ${!value ? 'bg-muted' : ''}`}
          >
            <div className="font-medium text-muted-foreground">All Members</div>
            <div className="mt-0.5 text-[11px] text-muted-foreground">
              Receive from anyone in group
            </div>
          </button>
          {members.map((member, index) => (
            <button
              key={member.jid || member.phone}
              onClick={() => handleSelectMember(member)}
              className={`w-full cursor-pointer px-3 py-2.5 text-left text-[13px] text-foreground transition-colors hover:bg-muted ${value === member.phone ? 'bg-muted' : ''} ${index < members.length - 1 ? 'border-b border-border' : ''}`}
            >
              <div className="font-medium">
                {member.name || member.phone}
                {member.is_admin && <span className="ml-2 text-[10px] text-warning">(Admin)</span>}
              </div>
              <div className="mt-0.5 font-mono text-[11px] text-muted-foreground">
                {member.phone}
              </div>
            </button>
          ))}
          <button
            onClick={() => setShowDropdown(false)}
            className="w-full cursor-pointer border-t border-border bg-muted px-3 py-2 text-center text-xs text-muted-foreground transition-colors hover:text-foreground"
          >
            Close
          </button>
        </div>
      )}
    </div>
  );
};

// Channel JID Selector - with Load Channels button and dropdown
const ChannelJidSelector: React.FC<{
  requestKey: string;
  value: string;
  onChange: (value: string) => void;
  onNameChange?: (name: string) => void;
  storedName?: string;
  placeholder?: string;
  isDragOver: boolean;
  onDragOver: (e: React.DragEvent) => void;
  onDragLeave: (e: React.DragEvent) => void;
  onDrop: (e: React.DragEvent) => void;
}> = ({ requestKey, value, onChange, onNameChange, storedName, placeholder, isDragOver, onDragOver, onDragLeave, onDrop }) => {
  const [channels, setChannels] = useState<Array<{ jid: string; name: string; subscriber_count?: number; role?: string }>>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [showDropdown, setShowDropdown] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [localChannelName, setLocalChannelName] = useState<string | null>(null);
  const selectedChannelName = storedName || localChannelName;
  const { getWhatsAppChannels } = useWebSocket();
  const requestGeneration = React.useRef(0);
  const activeRequestKey = React.useRef(requestKey);
  activeRequestKey.current = requestKey;

  useEffect(() => {
    requestGeneration.current += 1;
    setChannels([]);
    setLocalChannelName(null);
    setShowDropdown(false);
    setError(null);
    setIsLoading(false);
    return () => {
      requestGeneration.current += 1;
    };
  }, [requestKey]);

  useEffect(() => {
    if (storedName) setLocalChannelName(storedName);
  }, [storedName]);

  const handleLoadChannels = async () => {
    const request = ++requestGeneration.current;
    const requestKeyAtStart = requestKey;
    const isCurrent = () => (
      request === requestGeneration.current
      && activeRequestKey.current === requestKeyAtStart
    );
    setIsLoading(true);
    setError(null);
    try {
      const result = await getWhatsAppChannels();
      if (!isCurrent()) return;
      if (result.success && result.channels.length > 0) {
        setChannels(result.channels);
        setShowDropdown(true);
        if (value) {
          const match = result.channels.find(c => c.jid === value);
          if (match && match.name !== storedName) {
            setLocalChannelName(match.name);
            onNameChange?.(match.name);
          }
        }
      } else if (result.error) {
        setError(result.error);
      } else {
        setError('No channels found');
      }
    } catch (err: any) {
      if (isCurrent()) {
        setError(err.message || 'Failed to load channels');
      }
    } finally {
      if (isCurrent()) {
        setIsLoading(false);
      }
    }
  };

  const handleSelectChannel = (ch: { jid: string; name: string }) => {
    onChange(ch.jid);
    setLocalChannelName(ch.name);
    onNameChange?.(ch.name);
    setShowDropdown(false);
  };

  const handleInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    onChange(e.target.value);
    setLocalChannelName(null);
    onNameChange?.('');
  };

  const displayValue = selectedChannelName || value;
  const isSelected = selectedChannelName !== null && value;
  const isTemplate = !!(value && value.includes('{{'));

  return (
    <div className="relative">
      <div className="flex items-center gap-2">
        <ShadcnInput
          type="text"
          value={displayValue}
          onChange={handleInputChange}
          placeholder={placeholder || '120363...@newsletter or https://whatsapp.com/channel/...'}
          onDragOver={onDragOver}
          onDragLeave={onDragLeave}
          onDrop={onDrop}
          className={`flex-1 ${isDragOver ? 'border-ring ring-3 ring-ring/50 bg-accent/10' : ''} ${isSelected ? 'bg-muted text-success font-medium' : (isTemplate ? 'text-info font-mono' : '')}`}
        />
        <ActionButton
          intent="config"
          onClick={handleLoadChannels}
          disabled={isLoading}
          title="Load WhatsApp channels"
        >
          {isLoading ? 'Loading...' : 'Load'}
        </ActionButton>
      </div>
      {isSelected && (
        <div className="mt-1 font-mono text-xs text-muted-foreground">
          {value}
        </div>
      )}
      {error && (
        <div className="mt-1 text-xs text-destructive">
          {error}
        </div>
      )}
      {showDropdown && channels.length > 0 && (
        <div className="absolute left-0 right-0 top-full z-[1000] mt-1 max-h-[200px] overflow-y-auto rounded-md border border-border bg-background shadow-sm">
          {channels.map((ch, i) => (
            <button
              key={ch.jid}
              onClick={() => handleSelectChannel(ch)}
              className={`w-full cursor-pointer px-3 py-2.5 text-left text-[13px] text-foreground transition-colors hover:bg-muted ${i < channels.length - 1 ? 'border-b border-border' : ''}`}
            >
              <div className="font-medium">{ch.name}</div>
              <div className="mt-0.5 font-mono text-xs text-muted-foreground">
                {ch.jid}
                {ch.subscriber_count != null && <span className="ml-2">({ch.subscriber_count} subscribers)</span>}
              </div>
            </button>
          ))}
          <button
            onClick={() => setShowDropdown(false)}
            className="w-full cursor-pointer border-t border-border bg-muted px-3 py-2 text-center text-[13px] text-muted-foreground transition-colors hover:text-foreground"
          >
            Close
          </button>
        </div>
      )}
    </div>
  );
};

// Fixed Collection Renderer - n8n style fixed collection
const FixedCollectionRenderer: React.FC<{
  parameter: any;
  value: any;
  onChange: (value: any) => void;
  allParameters?: Record<string, any>;
}> = ({ parameter, value, onChange, allParameters }) => {
  const currentValue = value || {};

  return (
    <div className="rounded-md border border-border bg-muted p-3">
      {parameter.options?.map((option: any) => {
        const optionValue = currentValue[option.name] || {};

        return (
          <div key={option.name} className="mb-4">
            <div className="mb-2 text-sm font-medium text-foreground">
              {option.displayName}
            </div>
            <div className="rounded-md border border-border bg-background p-3">
              {option.values?.map((valueParam: any) => {
                // Wave 10.G.1: propagate `displayOptions.show` into nested
                // fixedCollection renders. Without this, sub-parameters with
                // conditional visibility render unconditionally inside the
                // collection, showing fields whose gate isn't satisfied.
                // The visibility context is the merged {allParameters, option
                // sub-values}, so a `show: {<siblingInCollection>: [...]}`
                // can gate on sibling values too.
                if (!shouldShowParameter(valueParam, { ...allParameters, ...optionValue })) {
                  return null;
                }
                return (
                  <ParameterRenderer
                    key={valueParam.name}
                    parameter={valueParam}
                    value={optionValue[valueParam.name]}
                    onChange={(newValue) => {
                      onChange({
                        ...currentValue,
                        [option.name]: {
                          ...optionValue,
                          [valueParam.name]: newValue,
                        },
                      });
                    }}
                    allParameters={allParameters}
                  />
                );
              })}
            </div>
          </div>
        );
      })}
    </div>
  );
};

interface ParameterRendererProps {
  parameter: NodeParameter | INodeProperties;
  value: any;
  onChange: (value: any) => void;
  allParameters?: Record<string, any>;
  onParameterChange?: (paramName: string, value: any) => void;
  onClosePanel?: () => void;
  isLoadingParameters?: boolean;
  /** For memory nodes: the connected agent's node ID for auto-session display */
  connectedAgentId?: string | null;
}

// Type guard to check if parameter is INodeProperties
const isINodeProperties = (param: NodeParameter | INodeProperties): param is INodeProperties => {
  return 'typeOptions' in param;
};

const ParameterRenderer: React.FC<ParameterRendererProps> = ({
  parameter,
  value,
  onChange,
  allParameters,
  onParameterChange,
  isLoadingParameters = false,
  connectedAgentId,
}) => {
  // Don't use default while loading - wait for actual saved value to load
  // This prevents showing template code briefly before saved code appears
  const currentValue = isLoadingParameters ? (value ?? '') : (value !== undefined ? value : parameter.default);
  const [isDragOver, setIsDragOver] = useState(false);
  const [dynamicOptions, setDynamicOptions] = useState<INodePropertyOption[]>([]);
  const [nodeParameters, setNodeParameters] = useState<Record<string, any>>({});

  const selectedNode = useAppStore((s) => s.selectedNode);
  const { getNodeParameters, sendRequest } = useWebSocket();
  const { getStoredApiKey, hasStoredKey, getStoredModels, getProviderDefaults } = useApiKeys();
  const activeNodeIdRef = React.useRef<string | null>(selectedNode?.id ?? null);
  activeNodeIdRef.current = selectedNode?.id ?? null;
  const onChangeRef = React.useRef(onChange);
  onChangeRef.current = onChange;

  // Don't render hidden parameters
  if (parameter.type === 'hidden') {
    return null;
  }

  // Load node parameters for expression resolution
  useEffect(() => {
    const nodeId = selectedNode?.id;
    let cancelled = false;

    if (!nodeId) {
      setNodeParameters({});
      return () => {
        cancelled = true;
      };
    }

    // Never render a previous node's derived parameter snapshot while the
    // request for the newly selected node is pending.
    setNodeParameters({});
    const loadParameters = async () => {
      const result = await getNodeParameters(nodeId);
      if (!cancelled && activeNodeIdRef.current === nodeId && result?.parameters) {
        setNodeParameters(result.parameters);
      }
    };
    void loadParameters();
    return () => {
      cancelled = true;
    };
  }, [selectedNode?.id, getNodeParameters]);

  // Auto-load stored API key and models when provider changes
  // Use ref to track previous provider to prevent infinite loops
  const prevProviderRef = React.useRef<string | null>(null);
  // Track if we've done initial auto-select after parameters loaded
  const hasAutoSelectedRef = React.useRef(false);

  // Reset auto-select tracking when node changes
  useEffect(() => {
    hasAutoSelectedRef.current = false;
    prevProviderRef.current = null;
    setDynamicOptions([]);
  }, [selectedNode?.id]);

  useEffect(() => {
    const nodeId = selectedNode?.id;
    const selectedNodeType = selectedNode?.type || selectedNode?.data?.nodeType;
    let cancelled = false;
    const isCurrent = () => !cancelled && !!nodeId && activeNodeIdRef.current === nodeId;

    const loadStoredKeyForProvider = async () => {
      // Only run for api_key or model parameters. Schema-canonical
      // name from the chat-model `_base.py` Pydantic model is
      // `api_key` (snake_case).
      if (parameter.name !== 'api_key' && parameter.name !== 'model') return;

      // Don't run while parameters are still loading from database
      if (isLoadingParameters) return;

      // Get provider from allParameters or derive from node type
      let provider = allParameters?.provider;
      if (!provider && selectedNodeType) {
        if (selectedNodeType) {
          provider = NODE_TYPE_TO_PROVIDER[selectedNodeType];
        }
      }
      if (!provider || !nodeId || !isCurrent()) return;

      // Distinguish between initial load (prevProvider was null) and actual user-initiated provider change
      // On initial load: respect saved model if it exists
      // On provider change: reset to first model to prevent mismatched provider/model
      const isInitialLoad = prevProviderRef.current === null;
      const isActualProviderChange = !isInitialLoad && prevProviderRef.current !== provider;
      const shouldAutoSelectModel = parameter.name === 'model' &&
        (isActualProviderChange || isInitialLoad || !hasAutoSelectedRef.current);

      // Skip if provider hasn't changed (except for initial model load)
      if (!isActualProviderChange && !isInitialLoad && parameter.name !== 'model') return;
      if (!isActualProviderChange && !isInitialLoad && hasAutoSelectedRef.current) return;

      prevProviderRef.current = provider;

      try {
        const hasKey = await hasStoredKey(provider);
        if (!isCurrent()) return;

        if (hasKey) {
          // Auto-load API key for api_key parameter - always update when provider changes
          if (parameter.name === 'api_key' && isActualProviderChange) {
            const storedKey = await getStoredApiKey(provider);
            if (!isCurrent()) return;
            if (storedKey) {
              onChangeRef.current(storedKey);
            }
          }

          // Auto-load models for model parameter
          if (shouldAutoSelectModel) {
            const models = await getStoredModels(provider);
            if (!isCurrent()) return;
            if (models?.length) {
              const modelOptions = DynamicParameterService.createModelOptions(models);

              // Get the configured default model for this provider
              const providerDefaults = await getProviderDefaults(provider);
              if (!isCurrent()) return;
              const configuredDefaultModel = providerDefaults?.default_model || '';

              DynamicParameterService.updateParameterOptions(nodeId, 'model', modelOptions);

              // Extract model ID (handles both string and object formats)
              const getModelId = (model: any) => typeof model === 'string' ? model : model.id;

              // Find the default model in the available models list, or fall back to first model
              let defaultModelToUse = getModelId(models[0]);
              if (configuredDefaultModel) {
                const matchingModel = models.find(m => getModelId(m) === configuredDefaultModel);
                if (matchingModel) {
                  defaultModelToUse = getModelId(matchingModel);
                }
              }

              // When user actively changes provider, reset to default model
              // to prevent mismatched provider/model combinations (e.g., OpenAI model with Anthropic provider)
              if (isActualProviderChange) {
                onChangeRef.current(defaultModelToUse);
              } else {
                // Initial load or no provider change - only auto-select if no saved model exists
                const savedModel = value || allParameters?.model;
                if (!savedModel || savedModel === '') {
                  onChangeRef.current(defaultModelToUse);
                }
                // If saved model exists, keep it (don't call onChange)
              }
              hasAutoSelectedRef.current = true;
            }
          }
        } else {
          if (!isCurrent()) return;
          // No stored key for this provider - clear the fields
          if (parameter.name === 'api_key') {
            onChangeRef.current('');
          }
          if (parameter.name === 'model') {
            onChangeRef.current('');
            hasAutoSelectedRef.current = true;
          }
        }
      } catch (error) {
        if (isCurrent()) {
          console.warn('Error loading stored key info:', error);
        }
      }
    };

    void loadStoredKeyForProvider();
    return () => {
      cancelled = true;
    };
  }, [allParameters?.provider, parameter.name, hasStoredKey, getStoredApiKey, getStoredModels, getProviderDefaults, selectedNode?.id, selectedNode?.type, isLoadingParameters, value, allParameters?.model]);

  // Merge database params with current form params (current takes precedence)
  const resolvedParameters = { ...nodeParameters, ...allParameters };

  // Helper functions to get values from both interface types
  const getMin = () => (parameter as any).min || (parameter as any).typeOptions?.minValue || 0;
  const getMax = () => (parameter as any).max || (parameter as any).typeOptions?.maxValue || 100;
  const getStep = () => (parameter as any).step || (parameter as any).typeOptions?.numberStepSize || 1;

  // Load dynamic options based on loadOptionsMethod
  useEffect(() => {
    const nodeId = selectedNode?.id;
    const nodeType = selectedNode?.data?.nodeType || selectedNode?.type;
    const method = isINodeProperties(parameter) ? parameter.typeOptions?.loadOptionsMethod : undefined;
    let cancelled = false;
    const isCurrent = () => !cancelled && !!nodeId && activeNodeIdRef.current === nodeId;

    const loadDynamicOptions = async () => {
      if (!nodeId || !nodeType || !isINodeProperties(parameter) || !method) return;

      const dependsOn = parameter.typeOptions?.loadOptionsDependsOn || [];
      const allParamsResolved = { ...nodeParameters, ...allParameters };

      // Check if all dependencies are satisfied
      const hasAllDependencies = dependsOn.every((dep: string) => allParamsResolved[dep]);
      if (dependsOn.length > 0 && !hasAllDependencies) return;

      try {
        // Get the node definition to access methods
        const nodeDef = nodeType ? resolveNodeDescription(nodeType) : null;

        let rawOptions: Array<{ value: any; name?: string; label?: string }> = [];

        if (nodeDef?.methods?.loadOptions?.[method]) {
          // Legacy frontend-defined loader (rare — most nodes are slimmed).
          const loadMethod = nodeDef.methods.loadOptions[method];
          const context = {
            getCurrentNodeParameter: (paramName: string) => allParamsResolved[paramName],
          };
          rawOptions = await loadMethod.call(context);
        } else {
          // Wave 10.G.1: dispatch to backend loader via the unified
          // `load_options` WS handler (server/routers/websocket.py:281).
          // Every non-WhatsApp loadOptionsMethod goes through here now —
          // Google Workspace (gmailLabels / googleCalendarList / etc.),
          // Android services, and any future backend-registered loader.
          try {
            const res = await sendRequest<{ options: Array<{ value: any; label?: string }> }>(
              'load_options',
              {
                method,
                params: {
                  node_id: nodeId,
                  node_type: nodeType,
                  ...allParamsResolved,
                },
              },
            );
            if (!isCurrent()) return;
            rawOptions = res?.options ?? [];
          } catch (err) {
            if (isCurrent()) {
              console.error(`[ParameterRenderer] backend load_options(${method}) failed:`, err);
            }
          }
        }

        if (!isCurrent()) return;

        // Backend returns `{value, label}`; INodePropertyOption wants
        // `{name, value}`. Normalise while preserving the original label.
        const options = rawOptions.map(o => ({
          name: o.name ?? o.label ?? String(o.value),
          value: o.value,
        }));

        setDynamicOptions(options);
        DynamicParameterService.updateParameterOptions(nodeId, parameter.name, options);
        if (options.length > 0 && (!currentValue || currentValue === '')) {
          onChangeRef.current(options[0].value);
        }
      } catch (error) {
        if (isCurrent()) {
          console.error('Error loading dynamic options:', error);
        }
      }
    };

    void loadDynamicOptions();
    return () => {
      cancelled = true;
    };
  }, [selectedNode?.id, isINodeProperties(parameter) && parameter.typeOptions?.loadOptionsMethod, nodeParameters, allParameters, parameter.name]);

  // Load default parameters for Android service nodes when service_id or action changes
  useEffect(() => {
    const nodeId = selectedNode?.id;
    const nodeType = selectedNode?.data?.nodeType || selectedNode?.type;
    const allParamsResolved = { ...nodeParameters, ...allParameters };
    const serviceId = allParamsResolved.service_id;
    const action = allParamsResolved.action;
    const abortController = new AbortController();
    let cancelled = false;
    const isCurrent = () => !cancelled && !!nodeId && activeNodeIdRef.current === nodeId;

    const loadDefaultParameters = async () => {
      if (!nodeId || !nodeType || parameter.name !== 'parameters') return;

      // Wave 10.E: backend group membership with bundled-definition fallback
      const isAndroid = isNodeInBackendGroup(nodeType, 'android')
        ?? (resolveNodeDescription(nodeType)?.group ?? []).includes('android');
      if (!isAndroid) return;

      if (!serviceId || !action) {
        console.log('[AndroidService] Skipping - missing serviceId or action:', { serviceId, action });
        return;
      }

      try {
        console.log('[AndroidService] Fetching default parameters for:', { serviceId, action });
        const response = await fetch(`${API_CONFIG.PYTHON_BASE_URL}/api/android/services/${serviceId}/actions/${action}/parameters`, {
          credentials: 'include',
          signal: abortController.signal,
        });
        const data = await response.json();
        if (!isCurrent()) return;
        console.log('[AndroidService] Default parameters response:', data);

        if (data.success && data.default_parameters) {
          // Always update with new defaults when service/action changes
          console.log('[AndroidService] Setting parameters to:', data.default_parameters);
          onChangeRef.current(data.default_parameters);
        }
      } catch (error) {
        if (isCurrent() && (error as Error)?.name !== 'AbortError') {
          console.error('[AndroidService] Error loading default parameters:', error);
        }
      }
    };

    void loadDefaultParameters();
    return () => {
      cancelled = true;
      abortController.abort();
    };
  }, [
    selectedNode?.id,
    parameter.name,
    allParameters?.service_id,
    allParameters?.action,
    nodeParameters?.service_id,
    nodeParameters?.action
  ]);

  // Subscribe to dynamic parameter updates
  useEffect(() => {
    const nodeId = selectedNode?.id;
    if (!nodeId) return;


    const unsubscribe = DynamicParameterService.subscribe((updatedNodeId, parameterName, options) => {

      if (updatedNodeId === nodeId && activeNodeIdRef.current === nodeId && parameterName === parameter.name) {
        setDynamicOptions(options);
      }
    });

    // Check for existing dynamic options
    const existingOptions = DynamicParameterService.getParameterOptions(nodeId, parameter.name);

    if (existingOptions) {
      setDynamicOptions(existingOptions);
    }

    return unsubscribe;
  }, [selectedNode?.id, parameter.name]);

  // Handle API key validation success
  const handleApiKeyValidationSuccess = (models: string[]) => {
    const nodeId = selectedNode?.id;
    if (!nodeId || activeNodeIdRef.current !== nodeId) {
      console.warn('ParameterRenderer: Ignoring validation result for an inactive node');
      return;
    }

    // Always update the 'model' parameter with dynamic options when API key validation succeeds
    // This callback can be triggered from any parameter (usually the apiKey parameter)
    const modelOptions = DynamicParameterService.createModelOptions(models);
    DynamicParameterService.updateParameterOptions(nodeId, 'model', modelOptions);

    // If this callback is triggered from the model parameter itself and it's empty, auto-select first model
    if (parameter.name === 'model' && !currentValue && models.length > 0) {
      onChangeRef.current(models[0]);
    }
  };

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'copy';
    setIsDragOver(true);
  };

  const handleDragLeave = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragOver(false);
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragOver(false);

    // Check if this is a coordinate parameter (lat/lng) for special handling
    const paramName = parameter.name.toLowerCase();
    const isCoordinate = paramName.includes('lat') || paramName.includes('lng') ||
                        paramName.includes('lon') || paramName === 'latitude' ||
                        paramName === 'longitude';

    // Try to get JSON data first (from connected node outputs)
    const jsonData = e.dataTransfer.getData('application/json');
    if (jsonData) {
      try {
        const parsedData = JSON.parse(jsonData);
        if (parsedData.type === 'nodeVariable') {
          // For coordinate parameters, try to extract actual numeric value from connected node data
          if (isCoordinate && typeof parsedData.dataType === 'string' && parsedData.dataType === 'number') {
            // Look for actual coordinate values in global execution data
            // This is a simplified approach - in production you'd want more robust data access

            // For now, use template string but mark it for coordinate processing
            const existingValue = currentValue || '';
            const needsSpace = existingValue && !existingValue.endsWith(' ') && existingValue.length > 0;
            const newValue = existingValue + (needsSpace ? ' ' : '') + parsedData.variableTemplate;
            onChange(newValue);
            return;
          }

          // Handle variable template data - use the template string
          const existingValue = currentValue || '';
          // Add smart spacing - add space if existing content doesn't end with space
          const needsSpace = existingValue && !existingValue.endsWith(' ') && existingValue.length > 0;
          const newValue = existingValue + (needsSpace ? ' ' : '') + parsedData.variableTemplate;
          onChange(newValue);
          return;
        }
        if (parsedData.type === 'nodeOutput') {
          // Handle node output data - use the actual value for direct mapping
          onChange(parsedData.value);
          return;
        }
      } catch (err) {
        console.warn('Failed to parse JSON drag data:', err);
      }
    }

    // Fallback to existing text/plain format (OutputPanel drag-drop)
    const data = e.dataTransfer.getData('text/plain');
    if (data && data.startsWith('{{') && data.endsWith('}}')) {
      // For coordinate parameters, allow template strings but process them appropriately
      if (isCoordinate) {
        onChange(data); // Set the template directly for coordinate resolution
        return;
      }

      // Append to existing content instead of replacing
      const existingValue = currentValue || '';
      // Add smart spacing - add space if existing content doesn't end with space
      const needsSpace = existingValue && !existingValue.endsWith(' ') && existingValue.length > 0;
      const newValue = existingValue + (needsSpace ? ' ' : '') + data;
      onChange(newValue);
    }
  };

  const renderInput = () => {
    switch (parameter.type) {
      case 'string':
        // Check if this should be a textarea based on typeOptions.rows
        const shouldUseTextarea = (parameter as any).typeOptions?.rows > 1;
        // Check if this should be a password field
        const isPassword = (parameter as any).typeOptions?.password;
        // Check if this is a code editor
        const isCodeEditor = (parameter as any).typeOptions?.editor === 'code';

        // Wave 10.G.1: `password: true` must win over multi-row textarea —
        // a multi-line field (e.g. rows:1 + password:true) used to render as
        // plain textarea because the textarea branch below never checked
        // `isPassword`. Downgrade to `<input type="password">` in that case.
        if (shouldUseTextarea && isPassword) {
          // fall through to the non-textarea branch so masking is honoured.
        } else if (shouldUseTextarea) {
          // Use CodeEditor for code editing
          if (isCodeEditor) {
            // Show loading state while parameters are being fetched
            if (isLoadingParameters) {
              return (
                <div className="flex h-full min-h-[200px] items-center justify-center rounded-md border border-border bg-muted text-sm text-muted-foreground">
                  Loading code...
                </div>
              );
            }
            // Get language from typeOptions or default to python
            const codeLanguage = (parameter as any).typeOptions?.editorLanguage || 'python';
            return (
              <CodeEditor
                value={currentValue || ''}
                onChange={onChange}
                language={codeLanguage}
                placeholder={parameter.placeholder}
              />
            );
          }

          // Regular textarea for non-code
          const isTextareaTemplate = !!(currentValue && currentValue.includes('{{'));
          return (
            <Textarea
              value={currentValue || ''}
              onChange={(e) => onChange(e.target.value)}
              placeholder={parameter.placeholder}
              rows={(parameter as any).typeOptions?.rows || 3}
              spellCheck={true}
              onDragOver={handleDragOver}
              onDragLeave={handleDragLeave}
              onDrop={handleDrop}
              className={`min-h-[80px] resize-y leading-relaxed ${isDragOver ? 'border-ring ring-3 ring-ring/50 bg-accent/10' : ''} ${isTextareaTemplate ? 'text-info font-mono' : ''}`}
            />
          );
        }

        // Check if this parameter has API key validation
        const validationArray = (parameter as any).validation;
        const apiKeyValidation = validationArray?.find((v: any) => v.type === 'apiKey' && v.showValidateButton);

        if (apiKeyValidation) {
          // Resolve provider expression if it's a template like {{ $parameter["provider"] }}
          let resolvedProvider = apiKeyValidation.provider;
          if (typeof resolvedProvider === 'string' && resolvedProvider.includes('$parameter[')) {
            // Extract parameter name from expression like {{ $parameter["provider"] }}
            const match = resolvedProvider.match(/\$parameter\["([^"]+)"\]|\$parameter\['([^']+)'\]/);
            if (match) {
              const paramName = match[1] || match[2];
              resolvedProvider = resolvedParameters[paramName] || resolvedProvider;
            }
          }

          const resolvedValidationConfig = {
            ...apiKeyValidation,
            provider: resolvedProvider
          };

          return (
            <APIKeyValidator
              requestKey={`${selectedNode?.id ?? 'unselected'}:${String(resolvedProvider ?? '')}`}
              value={currentValue || ''}
              onChange={onChange}
              placeholder={parameter.placeholder}
              validationConfig={resolvedValidationConfig}
              onValidationSuccess={handleApiKeyValidationSuccess}
              isDragOver={isDragOver}
              onDragOver={handleDragOver}
              onDragLeave={handleDragLeave}
              onDrop={handleDrop}
            />
          );
        }

        // Check if this parameter has dynamic options (like models after API key validation)

        if (dynamicOptions.length > 0 && parameter.type === 'string') {
          // Check if OpenRouter (has [FREE] tagged models)
          const hasFreeModels = dynamicOptions.some(opt => String(opt.label || opt.value).includes('[FREE]'));

          if (hasFreeModels) {
            // Group into Free and Paid for OpenRouter using shadcn Select with groups
            const freeModels = dynamicOptions.filter(opt => String(opt.label || opt.value).includes('[FREE]'));
            const paidModels = dynamicOptions.filter(opt => !String(opt.label || opt.value).includes('[FREE]'));

            return (
              <Select
                value={currentValue || ''}
                onValueChange={(v) => onChange(v)}
              >
                <SelectTrigger className="w-full">
                  <SelectValue placeholder="Select a model..." />
                </SelectTrigger>
                <SelectContent>
                  <SelectGroup>
                    <SelectLabel>{`Free Models (${freeModels.length})`}</SelectLabel>
                    {freeModels.map((option) => (
                      <SelectItem key={String(option.value)} value={String(option.value)}>
                        {option.label || option.name || String(option.value)}
                      </SelectItem>
                    ))}
                  </SelectGroup>
                  <SelectGroup>
                    <SelectLabel>{`Paid Models (${paidModels.length})`}</SelectLabel>
                    {paidModels.map((option) => (
                      <SelectItem key={String(option.value)} value={String(option.value)}>
                        {option.label || option.name || String(option.value)}
                      </SelectItem>
                    ))}
                  </SelectGroup>
                </SelectContent>
              </Select>
            );
          }

          // Use shadcn Select for non-OpenRouter (original working code)
          return (
            <Select
              value={currentValue || ''}
              onValueChange={(v) => onChange(v)}
            >
              <SelectTrigger className="w-full">
                <SelectValue placeholder="Select a model..." />
              </SelectTrigger>
              <SelectContent>
                {dynamicOptions.map((option) => (
                  <SelectItem key={String(option.value)} value={String(option.value)}>
                    {option.label || option.name || String(option.value)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          );
        }

        // Placeholder for future debug logging on the legacy `model` field
        // (currently no-op so the resolver fall-through is loud in the diff
        // if logic is added later).

        // Schema-driven WhatsApp selectors. Behaviour is keyed off
        // `typeOptions.loadOptionsMethod` set in the node definition,
        // not the parameter name. Legacy `parameter.name` fallback is
        // kept for any node not yet annotated; remove once Phase 5
        // sweeps all definitions.
        const loadMethod = (parameter as any).typeOptions?.loadOptionsMethod as string | undefined;
        if (loadMethod === 'whatsappGroups' || parameter.name === 'group_id') {
          const storedGroupName = allParameters?.group_name || '';
          return (
            <GroupIdSelector
              requestKey={selectedNode?.id ?? 'unselected'}
              value={currentValue || ''}
              onChange={onChange}
              onNameChange={(name) => onParameterChange?.('group_name', name)}
              storedName={storedGroupName}
              placeholder={parameter.placeholder}
              isDragOver={isDragOver}
              onDragOver={handleDragOver}
              onDragLeave={handleDragLeave}
              onDrop={handleDrop}
            />
          );
        }

        if (loadMethod === 'whatsappChannels' || parameter.name === 'channel_jid') {
          const storedChannelName = allParameters?.channel_display_name || '';
          return (
            <ChannelJidSelector
              requestKey={selectedNode?.id ?? 'unselected'}
              value={currentValue || ''}
              onChange={onChange}
              onNameChange={(name) => onParameterChange?.('channel_display_name', name)}
              storedName={storedChannelName}
              placeholder={parameter.placeholder}
              isDragOver={isDragOver}
              onDragOver={handleDragOver}
              onDragLeave={handleDragLeave}
              onDrop={handleDrop}
            />
          );
        }

        if (loadMethod === 'whatsappGroupMembers' || parameter.name === 'sender_number') {
          const groupId = resolvedParameters?.group_id || allParameters?.group_id || '';
          const storedSenderName = allParameters?.sender_name || '';
          return (
            <SenderNumberSelector
              requestKey={selectedNode?.id ?? 'unselected'}
              value={currentValue || ''}
              onChange={onChange}
              onNameChange={(name) => onParameterChange?.('sender_name', name)}
              storedName={storedSenderName}
              placeholder={parameter.placeholder}
              isDragOver={isDragOver}
              onDragOver={handleDragOver}
              onDragLeave={handleDragLeave}
              onDrop={handleDrop}
              groupId={groupId}
            />
          );
        }

        // Special case for the session-id parameter on a Memory node:
        // show the auto-derived session id (the connected agent's node
        // id) as the placeholder so the user can see what the backend
        // will actually use. Schema-canonical name is `session_id`
        // (Pydantic snake_case).
        //
        // The schema's `session_id: str = Field(default="default")`
        // means freshly-created nodes carry the literal string
        // "default" as the saved value, which would hide the
        // placeholder. The backend's `_build_memory_entry` already
        // treats `""` and `"default"` identically (auto-derive to the
        // connected agent's node id), so we mirror that contract on
        // display: when the value is the auto-derive sentinel, the
        // input renders empty and the placeholder shows the resolved
        // agent id instead.
        if (parameter.name === 'session_id' && connectedAgentId) {
          const isAutoDeriveSentinel =
            !currentValue || currentValue === 'default';
          const displayValue = isAutoDeriveSentinel ? '' : currentValue;
          const isSessionTemplate = !!(currentValue && currentValue.includes('{{'));

          return (
            <ShadcnInput
              type="text"
              value={displayValue}
              onChange={(e) => onChange(e.target.value)}
              placeholder={connectedAgentId}
              onDragOver={handleDragOver}
              onDragLeave={handleDragLeave}
              onDrop={handleDrop}
              className={`${isDragOver ? 'border-ring ring-3 ring-ring/50 bg-accent/10' : ''} ${isSessionTemplate ? 'text-info font-mono' : ''}`}
            />
          );
        }

        const isStringTemplate = !!(currentValue && currentValue.includes('{{'));
        return (
          <ShadcnInput
            type={isPassword ? "password" : "text"}
            value={currentValue || ''}
            onChange={(e) => onChange(e.target.value)}
            placeholder={parameter.placeholder}
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
            onDrop={handleDrop}
            className={`${isDragOver ? 'border-ring ring-3 ring-ring/50 bg-accent/10' : ''} ${isStringTemplate ? 'text-info font-mono' : ''}`}
          />
        );

      case 'number':

        return (
          <ShadcnInput
            type="number"
            value={currentValue !== undefined ? currentValue : (parameter.default || 0)}
            onChange={(e) => onChange(Number(e.target.value))}
            min={getMin()}
            max={getMax()}
            step={getStep()}
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
            onDrop={handleDrop}
            className={isDragOver ? 'border-ring ring-3 ring-ring/50 bg-accent/10' : ''}
          />
        );

      case 'boolean':
        return (
          <label className="flex cursor-pointer items-center gap-2 text-sm text-foreground">
            <Checkbox
              checked={currentValue || false}
              onCheckedChange={(checked) => onChange(checked === true)}
            />
            {parameter.displayName}
          </label>
        );

      case 'select':
      case 'options':
        // Use dynamic options if available, otherwise use static options
        const optionsToRender = dynamicOptions.length > 0 ? dynamicOptions : (parameter.options || []);
        const selectOptions = optionsToRender.filter((option): option is import('../types/INodeProperties').INodePropertyOption =>
          'value' in option
        );

        return (
          <Select
            value={(currentValue ?? parameter.default ?? '') === '' ? undefined : String(currentValue ?? parameter.default)}
            onValueChange={(v) => onChange(v)}
          >
            <SelectTrigger className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {selectOptions.map((option) => (
                <SelectItem key={String(option.value)} value={String(option.value)}>
                  {option.label || option.name || String(option.value)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        );

      case 'slider':
        const sliderValue = currentValue !== undefined ? currentValue : (parameter.default || 0);
        return (
          <div>
            <Slider
              min={getMin()}
              max={getMax()}
              step={getStep()}
              value={[Number(sliderValue)]}
              onValueChange={(vals) => onChange(Number(vals[0]))}
            />
            <div className="mt-1 text-center text-xs text-muted-foreground">
              {sliderValue}
              {parameter.type === 'slider' ? '%' : ''}
            </div>
          </div>
        );

      case 'percentage':
        const percentageValue = currentValue !== undefined ? currentValue : (parameter.default || 0);
        return (
          <div>
            <Slider
              min={getMin()}
              max={getMax()}
              step={getStep()}
              value={[Number(percentageValue)]}
              onValueChange={(vals) => onChange(Number(vals[0]))}
            />
            <div className="mt-1 text-center text-xs text-muted-foreground">
              {percentageValue}%
            </div>
          </div>
        );

      case 'text':
        const isTextTemplate = !!(currentValue && currentValue.includes('{{'));
        return (
          <ShadcnInput
            type="text"
            value={currentValue || ''}
            onChange={(e) => onChange(e.target.value)}
            placeholder={parameter.placeholder}
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
            onDrop={handleDrop}
            className={`${isDragOver ? 'border-ring ring-3 ring-ring/50 bg-accent/10' : ''} ${isTextTemplate ? 'text-info font-mono' : ''}`}
          />
        );

      case 'file':
        const fileInputRef = React.useRef<HTMLInputElement>(null);

        const handleFileUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
          const file = e.target.files?.[0];
          if (!file) return;

          const reader = new FileReader();
          reader.onload = () => {
            const base64 = (reader.result as string).split(',')[1]; // Remove data:mime;base64, prefix
            // Store as object with base64 data, filename, and mime type
            onChange({
              type: 'upload',
              data: base64,
              filename: file.name,
              mimeType: file.type || 'application/octet-stream'
            });
          };
          reader.readAsDataURL(file);
        };

        const isUploadedFile = currentValue && typeof currentValue === 'object' && currentValue.type === 'upload';

        // Determine file accept type based on context (e.g., message_type for WhatsApp)
        const getFileAcceptType = () => {
          const messageType = allParameters?.message_type;
          if (messageType) {
            switch (messageType) {
              case 'image':
                return 'image/*';
              case 'video':
                return 'video/*';
              case 'audio':
                return 'audio/*,.ogg,.opus,.mp3,.wav,.m4a';
              case 'document':
                return '.pdf,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.txt,.csv,.zip,.rar';
              case 'sticker':
                return 'image/webp,.webp';
              default:
                return (parameter as any).typeOptions?.accept || '*/*';
            }
          }
          return (parameter as any).typeOptions?.accept || '*/*';
        };

        const isFileTemplate = !!(currentValue && currentValue.includes?.('{{'));
        return (
          <div>
            <div className="flex items-center gap-2">
              <ShadcnInput
                type="text"
                value={isUploadedFile ? `[Uploaded] ${currentValue.filename}` : (currentValue || '')}
                onChange={(e) => onChange(e.target.value)}
                placeholder={parameter.placeholder || 'Enter file path or upload'}
                onDragOver={handleDragOver}
                onDragLeave={handleDragLeave}
                onDrop={handleDrop}
                readOnly={isUploadedFile}
                className={`flex-1 font-mono ${isDragOver ? 'border-ring ring-3 ring-ring/50 bg-accent/10' : ''} ${isUploadedFile ? 'bg-muted text-success' : (isFileTemplate ? 'text-info' : '')}`}
              />
              <input
                key={`file-input-${allParameters?.message_type || 'default'}`}
                ref={fileInputRef}
                type="file"
                onChange={handleFileUpload}
                style={{ display: 'none' }}
                accept={getFileAcceptType()}
              />
              <ActionButton
                intent="config"
                onClick={() => fileInputRef.current?.click()}
                title="Upload file"
              >
                Upload
              </ActionButton>
              {isUploadedFile && (
                <ActionButton
                  intent="stop"
                  onClick={() => {
                    onChange('');
                    if (fileInputRef.current) fileInputRef.current.value = '';
                  }}
                  title="Clear uploaded file"
                >
                  X
                </ActionButton>
              )}
            </div>
            <div className="mt-1 text-[11px] italic text-muted-foreground">
              {isUploadedFile
                ? `Size: ${(currentValue.data.length * 0.75 / 1024).toFixed(1)} KB | Type: ${currentValue.mimeType}`
                : 'Enter server path or click Upload to select a file'}
            </div>
          </div>
        );

      case 'array':
        const arrayValue = Array.isArray(currentValue) ? currentValue : [];
        return (
          <div>
            <div className="max-h-[120px] overflow-y-auto rounded-md border border-border bg-background">
              {parameter.options?.map((option) => (
                <label
                  key={option.value}
                  className="flex cursor-pointer items-center gap-2 border-b border-border px-3 py-2 text-sm text-foreground"
                >
                  <Checkbox
                    checked={arrayValue.includes(option.value)}
                    onCheckedChange={(checked) => {
                      if (checked === true) {
                        onChange([...arrayValue, option.value]);
                      } else {
                        onChange(arrayValue.filter((v: any) => v !== option.value));
                      }
                    }}
                  />
                  {option.label}
                </label>
              ))}
            </div>
            <div className="mt-1 text-[11px] text-muted-foreground">
              Selected: {arrayValue.length} item{arrayValue.length !== 1 ? 's' : ''}
            </div>
          </div>
        );

      case 'collection':
        return <CollectionRenderer parameter={parameter} value={currentValue} onChange={onChange} allParameters={allParameters} />;

      case 'fixedCollection':
        return <FixedCollectionRenderer parameter={parameter} value={currentValue} onChange={onChange} allParameters={allParameters} />;

      case 'notice':
        // Info/notice display - shows informational text without input
        return (
          <div className="rounded-md border border-border bg-muted px-3 py-2.5 text-[13px] leading-relaxed text-muted-foreground">
            {parameter.default || parameter.description || ''}
          </div>
        );

      case 'json':
        // JSON editor - textarea for JSON input
        const jsonRows = (parameter as any).typeOptions?.rows || 6;
        return (
          <Textarea
            value={currentValue || parameter.default || '{}'}
            onChange={(e) => onChange(e.target.value)}
            placeholder={parameter.placeholder || '{}'}
            rows={jsonRows}
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
            onDrop={handleDrop}
            className={`min-h-[100px] resize-y font-mono leading-relaxed ${isDragOver ? 'border-ring ring-3 ring-ring/50 bg-accent/10' : ''}`}
          />
        );

      case 'code': {
        // Wave 10.G.1: dedicated widget for Pydantic fields tagged
        // `json_schema_extra={"editor": "code"}`. Before this case the
        // adapter mapped them to INodeProperties.type='code' but the
        // switch had no handler — they rendered as
        // "Unsupported parameter type: code".
        const codeLanguage = (parameter as any).typeOptions?.editorLanguage || 'python';
        if (isLoadingParameters) {
          return (
            <div className="flex items-center justify-center rounded-md border border-border bg-muted text-muted-foreground text-sm min-h-[200px]">
              Loading code...
            </div>
          );
        }
        return (
          <CodeEditor
            value={currentValue || parameter.default || ''}
            onChange={onChange}
            language={codeLanguage}
            placeholder={parameter.placeholder}
          />
        );
      }

      case 'dateTime':
        // shadcn Input — see client/src/components/ui/input.tsx
        return (
          <ShadcnInput
            type="datetime-local"
            value={currentValue || parameter.default || ''}
            onChange={(e) => onChange(e.target.value)}
          />
        );

      default:
        return (
          <Alert variant="destructive">
            Unsupported parameter type: {parameter.type}
          </Alert>
        );
    }
  };

  return (
    <div className="flex h-full flex-col">
      {parameter.type !== 'boolean' && (
        <label className="mb-2 flex flex-shrink-0 items-center gap-1.5 text-[13px] font-semibold text-foreground">
          <span>{parameter.displayName}</span>
          {parameter.required && (
            <span className="text-sm font-bold text-destructive">*</span>
          )}
        </label>
      )}

      <div className="min-h-0 flex-1">
        {renderInput()}
      </div>

      {parameter.description && (
        <div className="mt-1.5 flex-shrink-0 pl-0.5 text-xs leading-relaxed text-muted-foreground">
          {parameter.description}
        </div>
      )}

    </div>
  );
};

export default ParameterRenderer;
