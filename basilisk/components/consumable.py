from __future__ import annotations

from typing import Optional, TYPE_CHECKING
import random
import math

from basilisk import actions, color

import basilisk.components.ai

from basilisk.components.base_component import BaseComponent
from basilisk.exceptions import Impossible
from basilisk.input_handlers import (
    ActionOrHandler,
    AreaRangedAttackHandler,
    SingleRangedAttackHandler,
    SingleDrillingProjectileAttackHandler,
    SingleProjectileAttackHandler,
    InventoryIdentifyHandler,
    InventoryRearrangeHandler
)
from basilisk.components.status_effect import *
import basilisk.tile_types as tile_types


if TYPE_CHECKING:
    from basilisk.entity import Actor, Item


class Consumable(BaseComponent):
    parent: Item

    def __init__(self):
        self.do_snake = False

    @property
    def modified_damage(self):
        return self.damage + self.engine.player.BILE

    def get_throw_action(self, consumer: Actor) -> Optional[ActionOrHandler]:
        """Try to return the action for this item."""
        return actions.ThrowItem(consumer, self.parent)

    def get_eat_action(self, consumer: Actor) -> Optional[ActionOrHandler]:
        """Try to return the action for this item."""
        return actions.ItemAction(consumer, self.parent)

    def start_activation(self,action):
        self.consume()
        self.activate(action)
        self.identify()
        self.snake()

    def activate(self, action: actions.ItemAction) -> None:
        """Invoke this items ability.

        `action` is the context for this activation.
        """
        raise NotImplementedError()

    def consume(self) -> None:
        """Remove the consumed item from its containing inventory."""
        if not self.parent in self.engine.player.inventory.items:
            self.parent.consume()
            return
        self.do_snake = True
        self.footprint = self.parent.xy
        self.start_at = self.parent.gamemap.engine.player.inventory.items.index(self.parent)
        self.parent.consume()

    def identify(self) -> None:
        self.parent.identified = True
    
    def snake(self) -> None:
        if not self.do_snake:
            return
        self.engine.player.snake(self.footprint, self.start_at)

    def apply_status(self, action, status, duration=10) -> None:
        st = [s for s in action.target_actor.statuses if isinstance(s,status)]
        if st:
            st[0].strengthen()
        else:
            st = status(duration, action.target_actor)


class Projectile(Consumable):
    description = "launch a small projectile"

    def __init__(self,damage=1):
        super().__init__()
        self.damage = damage
        if damage > 4:
            descriptor = "large "
        elif damage > 2:
            descriptor = ""
        else:
            descriptor = "small "    
        self.description = f"launch a {descriptor}projectile"


    def get_throw_action(self, consumer: Actor, thru_tail=True) -> Optional[ActionOrHandler]:
        self.engine.message_log.add_message("Select a target.", color.cyan)
        seeking = "anything" #if not self.parent.identified else "actor"
        return SingleProjectileAttackHandler(
            self.engine,
            callback=lambda xy: actions.ThrowItem(consumer, self.parent, xy),
            seeking=seeking,
            thru_tail = thru_tail
        )

    def activate(self, action: actions.ItemAction) -> None:
        """ Override this part"""
        consumer = action.entity
        target = action.target_actor if action.target_actor else action.target_item

        if target:
            self.engine.message_log.add_message(
                    f"{target.label} takes {self.modified_damage} damage!", color.offwhite
            )
            target.take_damage(self.modified_damage)
        else:
            self.engine.message_log.add_message("Nothing happens.", color.grey)

    def consume(self) -> None:
        if any(isinstance(s,FreeSpit) for s in self.engine.player.statuses):
            return

        super().consume()


class DecoyConsumable(Projectile):
    description = "spawn a decoy"

    def __init__(self):
        self.do_snake = False

    def activate(self, action: actions.ItemAction) -> None:
        x,y = action.target_xy

        if not self.engine.game_map.tile_is_walkable(x,y):
            path = self.engine.player.ai.get_path_to(x,y,0)
            tiles = []
            for tile in path:
                if self.engine.game_map.tile_is_walkable(*tile):
                    tiles.append(tile)
            if len(tiles) > 0:
                x,y = tiles[-1]
            else:
                self.engine.message_log.add_message("With no room to swing its elbows, it burrows into the ground.", color.grey)
                return

        self.engine.message_log.add_message("It begins taunting your enemies!")
        d = self.engine.game_map.decoy.spawn(self.engine.game_map,x,y)
        Doomed(10,d)
        for actor in self.engine.game_map.actors:
            if actor.ai.fov[x,y]:
                actor.ai.clear_intent()


class TimeReverseConsumable(Consumable):
    description = "wrinkle time"

    def __init__(self):
        self.do_snake = False
        self.turns = 5

    def activate(self, action: actions.ItemAction) -> None:
        self.engine.message_log.add_message("You feel intense deja-vu.")
        self.engine.turn_back_time(self.turns,self.parent)


class WormholeConsumable(Projectile):
    description = "wrinkle space"

    def __init__(self):
        self.do_snake = False

    def get_throw_action(self, consumer: Actor):
        if not self.parent.identified:
            return super().get_throw_action(consumer)
        return SingleRangedAttackHandler(self.engine, lambda xy: actions.ThrowItem(consumer,self.parent,xy), True)

    def activate(self, action: actions.ItemAction) -> None:
        consumer = action.entity

        wormhole = None
        if not self.parent.identified and not self.engine.game_map.tile_is_walkable(*action.target_xy):
            path = consumer.ai.get_path_to(*action.target_xy,0)

            for tile in reversed(path):
                if not self.engine.game_map.tile_is_walkable(*tile, consumer.is_phasing):
                    continue
                wormhole = tile
                break
        elif not self.parent.identified:
            wormhole = action.target_xy

        if self.parent.identified and self.engine.game_map.tile_is_walkable(*action.target_xy):
            wormhole = action.target_xy

        if not wormhole:
            self.engine.message_log.add_message("Space stretches like taffy then snaps back to normalcy.")
            return

        self.engine.message_log.add_message("Space stretches like taffy and pulls you through it!")
        self.engine.player.place(*wormhole)

        for enemy in consumer.get_adjacent_actors():
            enemy.constrict()
        if action.target_item:
            actions.PickupAction(consumer).perform()

        # make both wormholes blocking until player's clear?
        # display: make relevant segments blue?


class EntanglingConsumable(Projectile):
    description = "make a stretch of ground snake-only"

    def __init__(self):
        self.do_snake = False
        self.radius = 3

    def get_throw_action(self, consumer: Actor):
        if not self.parent.identified:
            return super().get_throw_action(consumer)

        self.engine.message_log.add_message("Select a target location.", color.cyan)
        return AreaRangedAttackHandler(
            self.engine,
            radius=self.radius,
            callback=lambda xy: actions.ThrowItem(consumer,self.parent,xy)
        )

    def start_activation(self,action):
        if not self.engine.game_map.visible[action.target_xy]:
            raise Impossible("You cannot target an area that you cannot see.")
        super().start_activation(action)

    def activate(self, action: actions.ItemAction) -> None:
        x,y = action.target_xy

        for xi in range(x-self.radius,x+self.radius+1):
            for yi in range(y-self.radius,y+self.radius+1):
                if (
                    math.sqrt((xi-x) ** 2 + (yi-y) ** 2) <= self.radius and 
                    self.engine.game_map.tile_is_walkable(xi,yi) and
                    not self.engine.game_map.tiles[xi,yi] in (tile_types.down_stairs)
                ):
                    self.engine.game_map.tiles[(xi,yi)] = tile_types.snake_only

        self.engine.message_log.add_message("The area fills with terrain you are uniquely equipped to traverse.")


class SpittingConsumable(Projectile):
    description = "get spat"

    def __init__(self):
        self.do_snake = False

    def get_throw_action(self, consumer: Actor):
        if not self.parent.identified:
            return super().get_throw_action(consumer)

        return super().get_throw_action(consumer, thru_tail=False)

    def activate(self, action: actions.ItemAction) -> None:
        consumer = action.entity
        path = consumer.ai.get_path_to(*action.target_xy,0)

        for tile in path:
            if not self.engine.game_map.tile_is_snakeable(*tile, consumer.is_phasing):
                break
            dx = tile[0] - consumer.x
            dy = tile[1] - consumer.y
            consumer.move(dx,dy)

        self.engine.message_log.add_message("Scratch that. It spits you!")

        for enemy in consumer.get_adjacent_actors():
            enemy.constrict()
        if action.target_item:
            actions.PickupAction(consumer).perform()


class VacuumConsumable(Consumable):
    description="swallow all visible items"

    def activate(self, action: actions.ItemAction) -> None:
        to_swallow = [
            i for i in self.engine.game_map.items if 
                self.engine.game_map.visible[i.x,i.y] and 
                i not in self.engine.player.inventory.items
        ]

        if len(to_swallow) < 1:
            self.engine.message_log.add_message("Your stomach growls.")
            return

        self.engine.message_log.add_message("The resulting void attracts all nearby items!")
        for i in to_swallow:
            i.place(*action.entity.xy)
        actions.PickupAction(action.entity).perform()


class VacuumProjectile(Consumable):
    description="destroy all visible items"

    def __init__(self):
        self.do_snake = False

    def activate(self, action: actions.ItemAction) -> None:
        to_destroy = [
            i for i in self.engine.game_map.items if
            self.engine.game_map.visible[i.x,i.y] and
            i not in self.engine.player.inventory.items
        ]

        if len(to_destroy) < 1:
            self.engine.message_log.add_message("It whines loudly before popping out of existence.", color.grey)

        if len(to_destroy) > 0:
            self.engine.message_log.add_message("It cackles gleefully and disappears along with all nearby items.")
            for i in to_destroy:
                i.die()


class HookshotProjectile(Projectile):
    description = "hookshot an enemy or item"

    def __init__(self):
        self.do_snake = False

    def activate(self, action: actions.ItemAction) -> None:
        consumer = action.entity
        target = None

        if action.target_actor and action.target_actor is not consumer:
            target = action.target_actor
            self.engine.message_log.add_message(f"It pulls the {target.label} back to you!")
            tile = consumer.ai.get_path_to(*action.target_xy,0)[0]
            target.place(*tile)
            target.constrict()

        if not target and action.target_item and action.target_item not in consumer.inventory.items:
            target = action.target_item
            self.engine.message_log.add_message(f"It pulls the {target.label} back to you!")
            tile = consumer.xy
            target.place(*tile)
            actions.PickupAction(consumer).perform()

        if not target:
            self.engine.message_log.add_message("It unravels on the dungeon floor.")


class KnockbackProjectile(Projectile):
    description = "push back an enemy"

    def __init__(self,damage=2):
        self.damage = damage
        self.do_snake = False

    def activate(self, action: actions.ItemAction) -> None:
        consumer = action.entity
        target = action.target_actor

        if target:
            push_path = self.engine.player.ai.get_path_past(target.x,target.y,0)
            pushed = False
            destination = None
            for i,tile in enumerate(push_path):
                if not self.engine.game_map.tile_is_walkable(*tile) or i+1 > self.modified_damage:
                    break
                pushed = True
                destination = tile

            if pushed:
                target.place(*destination)
                self.engine.message_log.add_message(f"The {target.name} is slammed backward.")

            else:
                self.engine.message_log.add_message(f"The {target.name} couldn't be pushed.")
        else:
            self.engine.message_log.add_message("The forceful projectile dissipates in the air.")


class KnockbackConsumable(Consumable):
    description = "push back all adjacent enemies"

    def __init__(self,damage=2):
        self.damage=damage
        self.do_snake = False

    def activate(self, action: actions.ItemAction) -> None:
        consumer=action.entity
        pushed = self.knockback_from_segment(consumer,consumer)
        for i in consumer.inventory.items:
            if self.knockback_from_segment(i,consumer):
                pushed = True
        if not pushed:
            self.engine.message_log.add_message("The dust on the dungeon floor is swept away from you.")

    def knockback_from_segment(self,segment,consumer) -> None:
        pushed = False
        for actor in segment.get_adjacent_actors():
            if actor is consumer:
                continue
            d = (actor.x-segment.x,actor.y-segment.y)
            destination = None

            for i in range(self.damage):
                new_tile = (actor.x+d[0],actor.y+d[1]) if i == 0 else (destination[0]+d[0],destination[1]+d[1])
                if self.engine.game_map.tile_is_walkable(*new_tile):
                    destination = new_tile
                else:
                    break

            if destination:
                pushed = True
                actor.place(*destination)
                self.engine.message_log.add_message(f"The {actor.name} is slammed backward.")
        return pushed



class DrillingProjectile(Projectile):
    description = "pierce the dungeons"

    def __init__(self, damage=1):
        self.damage = damage
        self.do_snake = False

    def get_throw_action(self, consumer: Actor) -> Optional[ActionOrHandler]:
        if not self.parent.identified:
            return super().get_throw_action(consumer)
        else:
            self.engine.message_log.add_message("Select a target tile.", color.cyan)
            return SingleDrillingProjectileAttackHandler(
                self.engine,
                callback=lambda xy: actions.ThrowItem(consumer, self.parent, xy),
                walkable=False
            )

    def activate(self, action: actions.ItemAction) -> None:
        consumer = action.entity
        walkable = not self.parent.identified
        path = self.engine.player.ai.get_path_to(*action.target_xy,0,walkable)
        gm = self.engine.game_map

        for tile in path:
            actor = gm.get_actor_at_location(*tile)
            if actor and actor is not consumer:
                actor.take_damage(self.modified_damage)
                self.engine.message_log.add_message(f"It drills through the ?!", color.grey, actor.name, actor.color)

            if not gm.tiles['walkable'][tile[0],tile[1]]:
                gm.tiles[tile[0],tile[1]] = tile_types.floor
                self.engine.message_log.add_message("It drills through the dungeon wall!", color.grey)


class LeakingProjectile(Projectile):
    description = "make an enemy fall to pieces"

    def __init__(self):
        self.do_snake = False

    def get_throw_action(self, consumer: Actor) -> SingleRangedAttackHandler:
        if not self.parent.identified:
            return super().get_throw_action(consumer)

        self.engine.message_log.add_message("Select a target.", color.cyan)
        return SingleRangedAttackHandler(self.engine, callback=lambda xy: actions.ThrowItem(consumer, self.parent, xy))

    def start_activation(self,action):
        if not self.engine.game_map.visible[action.target_xy]:
            raise Impossible("You cannot target an area that you cannot see.")
        if action.target_actor is action.entity:
            raise Impossible("You cannot spit at yourself!")

        super().start_activation(action)

    def activate(self, action: actions.ItemAction) -> None:
        if not action.target_actor:
            self.engine.message_log.add_message("It splatters across the dungeon floor.",color.grey)

        if action.target_actor:
            self.apply_status(action, Leaking)


class DamageAllConsumable(Consumable):
    description = "deal damage to all enemies"

    def __init__(self,damage=1):
        self.damage = damage
        self.do_snake = False

    def activate(self, action: actions.ItemAction) -> None:
        consumer = action.entity

        actors = [a for a in self.engine.game_map.actors if self.engine.game_map.visible[a.x,a.y] and a is not self.engine.player]

        if len(actors) > 0:
            self.engine.message_log.add_message("You shower your opponents with acid rain!")
            for a in actors:
                a.take_damage(self.modified_damage)
        else:
            self.engine.message_log.add_message("You shower the dungeon with acid rain.",color.grey)


class ShieldingConsumable(Consumable):
    description = "shrug off the next hit you take"

    def activate(self, action: actions.ItemAction) -> None:
        self.apply_status(action,Shielded,1)


class PhasingConsumable(Consumable):
    description = "phase through walls"

    def activate(self, action: actions.ItemAction) -> None:
        self.apply_status(action,Phasing,4)

class PhasingProjectile(Projectile):
    description = "temporarily derealize an enemy"

    def __init__(self):
        self.do_snake = False

    def get_throw_action(self, consumer: Actor) -> SingleRangedAttackHandler:
        if not self.parent.identified:
            return super().get_throw_action(consumer)

        self.engine.message_log.add_message("Select a target.", color.cyan)
        return SingleRangedAttackHandler(self.engine, callback=lambda xy: actions.ThrowItem(consumer, self.parent, xy))

    def start_activation(self,action):
        if not self.engine.game_map.visible[action.target_xy]:
            raise Impossible("You cannot target an area that you cannot see.")
        if action.target_actor is action.entity:
            raise Impossible("You cannot spit at yourself!")

        super().start_activation(action)

    def activate(self, action: actions.ItemAction) -> None:
        if not action.target_actor:
            self.engine.message_log.add_message("A hole appears in the dungeon floor then disappears a moment later.",color.grey)

        if action.target_actor:
            self.apply_status(action, PhasedOut)

class NotConsumable(Consumable):
    description = "know futility"

    def consume(self):
        return

    def snake(self):
        return

    def activate(self, action: actions.ItemAction) -> None:
        self.engine.message_log.add_message("The segment refuses your command!", color.mind)


class StatBoostConsumable(Consumable):
    messages = {
        "BILE":"A more dangerous ? rises in your throat.",
        "MIND":"Time's weave floods the creases of your ?.",
        "TAIL":"Your ? thrashes with a new strength.",
        "TONG":"Your ? whips the air with increased sensitivity."
    }

    def __init__(self, amount, stat=None, permanent=False):
        super().__init__()
        self.stat = stat if stat else "a stat"
        self.amount = amount
        forever = " permanently" if permanent else ""
        self.description = f"increase {self.stat} by {amount}{forever}"
        self.permanent = permanent

    def activate(self, action: actions.ItemAction) -> None:
        stat = self.stat if self.stat != "a stat" else random.choice(['BILE','MIND','TAIL','TONG'])
        stat_str = "TONGUE" if stat == "TONG" else stat
        self.engine.message_log.add_message(self.messages[stat], color.grey, stat_str, color.stats[stat])
        if self.permanent:
            action.target_actor.base_stats[self.stat] += self.amount
        else:
            StatBoost(10, action.target_actor, stat, self.amount)


class FreeSpitConsumable(Consumable):
    description = "spit spit spit"

    def activate(self, action: actions.ItemAction) -> None:
        self.apply_status(action,FreeSpit,4)


class PetrifEyesConsumable(Consumable):
    description = "be your best self"

    def activate(self, action: actions.ItemAction) -> None:        
        self.apply_status(action, PetrifEyes, 4)


class ChokingConsumable(Consumable):
    description = "at your own risk"

    def activate(self, action: actions.ItemAction) -> None:
        self.engine.message_log.add_message("The segment bubbles up and gets caught in your throat!")
        self.apply_status(action, Choking)


class DroppingConsumable(Consumable):
    description = "drop everything"

    def activate(self, action: actions.ItemAction) -> None:
        self.engine.message_log.add_message("As you eject the segment you realize it was your lynchpin.")
        self.engine.message_log.add_message("You fall apart!", color.red)
        self.engine.player.unsnake(0)


class ConsumingConsumable(Consumable):
    description = "lose weight"

    def start_activation(self, action):
        self.activate(action)
        self.consume()
        self.identify()
        self.snake()

    def activate(self, action: actions.ItemAction) -> None:
        items = action.entity.inventory.items
        i = items.index(self.parent)
        neighbours = []
        if i > 0:
            neighbours.append(items[i-1])
        if i < len(items)-1:
            neighbours.append(items[i+1])

        if neighbours:
            neighbour = random.choice(neighbours)
            self.engine.message_log.add_message(f"It swipes your {neighbour.char} and disappears!", color.red)
            neighbour.edible.consume()
            neighbour.edible.identify()
            neighbour.edible.snake()
        else:
            self.engine.message_log.add_message("It makes a rude gesture and disappears.", color.grey)


class ReversingConsumable(Consumable):
    description = "turn around"

    def start_activation(self,action):
        self.activate(action)
        self.identify()
        self.snake()

    def activate(self, action: actions.ItemAction) -> None:
        # swap with the last /solid/ item
        # any that aren't solid stay at the end in reverse order
        consumer = action.entity
        tail = [i for i in consumer.inventory.items if i.blocks_movement][-1]
        x, y = tail.xy

        self.consume()

        items = consumer.inventory.items[:]

        solid_items = [i for i in items if i.blocks_movement]
        nonsolid_items = [i for i in items if not i.blocks_movement]

        solid_items.reverse()
        nonsolid_items.reverse()

        consumer.place(x,y)

        consumer.inventory.items = solid_items + nonsolid_items
        self.engine.check_word_mode()

        self.engine.message_log.add_message("You turn tail!", color.offwhite)


class ChangelingConsumable(Consumable):
    description = "???"

    def start_activation(self,action):
        self.activate(action)
        self.consume()
        self.identify()

    def activate(self, action: actions.ItemAction) -> None:
        # add new item to snake
        items = action.entity.inventory.items
        changeset = self.gamemap.item_factories
        new_i = random.choice(self.gamemap.item_factories).spawn(self.parent.gamemap,self.parent.x,self.parent.y)
        new_i.parent = action.entity
        items.insert(items.index(self.parent), new_i)
        new_i.solidify()
        self.engine.message_log.add_message(f"It turns into ?!", color.offwhite, new_i.char, new_i.color)


class IdentifyingConsumable(Consumable):
    description = "identify a segment on your tail"

    @property
    def can_identify(self):
        return any(i.identified == False and i.char != self.parent.char for i in self.engine.player.inventory.items)

    def get_eat_action(self, consumer: Actor) -> Optional[ActionOrHandler]:
        if self.can_identify:
            self.engine.message_log.add_message("Select a segment to identify.", color.cyan)
            return InventoryIdentifyHandler(self.engine, self.parent)

        return actions.ItemAction(consumer, self.parent)


    def activate(self, action:action.ItemAction) -> None:
        item = action.target_item
        if not item:
            self.engine.message_log.add_message("You feel nostalgic.", color.grey)
            return

        self.engine.message_log.add_message(f"You identified the {item.char}.", color.offwhite)
        item.identified = True


class IdentifyingProjectile(Projectile):
    description = "identify a segment on the ground"

    def __init__(self):
        self.do_snake = False

    def get_throw_action(self, consumer: Actor) -> Optional[ActionOrHandler]:
        if not self.parent.identified:
            return super().get_throw_action(consumer)

        self.engine.message_log.add_message("Select a target item.", color.cyan)
        return SingleRangedAttackHandler(
            self.engine,
            callback=lambda xy: actions.ThrowItem(consumer, self.parent, xy)
        )


    def activate(self, action:action.ItemAction) -> None:
        item = action.target_item
        if not item:
            self.engine.message_log.add_message("The segment shatters uselessly on the ground.", color.grey)
            return

        self.engine.message_log.add_message(f"You identified the {item.char}.", color.offwhite)
        item.identified = True


class RearrangingConsumable(Consumable):
    description = "rearrange yourself"

    @property
    def can_rearrange(self):
        return len(self.engine.player.inventory.items) > 2

    def get_eat_action(self, consumer: Actor) -> Optional[ActionOrHandler]:
        if self.can_rearrange:
            self.engine.message_log.add_message("Type out your new self.", color.cyan)
            return InventoryRearrangeHandler(self.engine, self.parent)

        return actions.ItemAction(consumer, self.parent)

    def activate(self, action:action.ItemAction) -> None:
        self.engine.message_log.add_message("You feel self-assured.", color.grey)


class NothingConsumable(Consumable):
    description = None

    def activate(self, action: action.ItemAction) -> None:
        self.engine.message_log.add_message("Your stomach rumbles.", color.grey)


class ThirdEyeBlindConsumable(Consumable):
    description = "blind your third eye"

    def activate(self, action: actions.ItemAction) -> None:
        self.engine.message_log.add_message("The segment dissolves in the air, leaving a shroud of temporal ambiguity.")
        self.apply_status(action, ThirdEyeBlind)


class PetrifyConsumable(Consumable):
    description = "petrify thyself"

    def activate(self, action: actions.ItemAction) -> None:
        self.engine.message_log.add_message("The taste of earth and bone permeates your being.")
        self.apply_status(action, PetrifiedSnake, 3)


class RandomProjectile(Projectile):
    description = "???"

    def __init__(self):
        self.do_snake = False

    def activate(self, action: actions.ItemAction) -> None:
        effect = copy.deepcopy(random.choice(self.gamemap.item_factories).spitable)
        self.parent.spitable = effect
        self.parent.spitable.activate(action)


class PetrifyEnemyConsumable(Projectile):
    description = "petrify an enemy"

    def __init__(self):
        self.do_snake = False

    def get_throw_action(self, consumer: Actor) -> SingleRangedAttackHandler:
        if not self.parent.identified:
            return super().get_throw_action(consumer)

        self.engine.message_log.add_message("Select a target.", color.cyan)
        return SingleRangedAttackHandler(self.engine, callback=lambda xy: actions.ThrowItem(consumer, self.parent, xy))

    def start_activation(self,action):
        if not self.engine.game_map.visible[action.target_xy]:
            raise Impossible("You cannot target an area that you cannot see.")
        if action.target_actor is action.entity:
            raise Impossible("You cannot spit at yourself!")

        super().start_activation(action)

    def activate(self, action: actions.ItemAction) -> None:
        if not action.target_actor:
            self.engine.message_log.add_message("The projectile breaks apart on the dungeon floor.",color.grey)

        if action.target_actor:
            self.apply_status(action, Petrified)


class ClingyConsumable(Projectile):
    description = ":("

    def __init__(self):
        self.do_snake = False

    def start_activation(self,action):
        self.activate(action)
        self.identify()

    def activate(self, action: actions.ItemAction) -> None:
        inv = self.parent.gamemap.engine.player.inventory.items
        index = inv.index(self.parent)
        xy = self.parent.xy

        if index == 0:
            self.plop(action)
            return

        other_index = index-1
        other_item = inv[other_index]

        inv[other_index] = self.parent
        inv[index] = other_item

        self.parent.place(*other_item.xy)
        other_item.place(*xy)

        self.parent.solidify()

        self.engine.message_log.add_message("The segment clings and whines, only moving forward a bit.")

    def plop(self, action: actions.ItemAction):
        xy = self.parent.xy
        space = self.engine.player.ai.get_path_to(*action.target_xy,0)[0]
        self.parent.desolidify()
        self.parent.place(*space)
        self.engine.player.snake(xy)
        self.engine.message_log.add_message("It plops down in front of you.")
        self.parent.identified = True




class ConfusionConsumable(Projectile):
    description = "confuse an enemy"

    def __init__(self, number_of_turns: int=10):
        self.number_of_turns = number_of_turns
        self.do_snake = False

    def get_throw_action(self, consumer: Actor) -> SingleRangedAttackHandler:
        if not self.parent.identified:
            return super().get_throw_action(consumer)

        self.engine.message_log.add_message(
            "Select a target.", color.cyan
        )
        return SingleRangedAttackHandler(
            self.engine,
            callback=lambda xy: actions.ThrowItem(consumer, self.parent, xy),
        )

    def start_activation(self,action):
        if not self.engine.game_map.visible[action.target_xy]:
            raise Impossible("You cannot target an area that you cannot see.")
        if action.target_actor is action.entity:
            raise Impossible("You cannot spit at yourself!")

        super().start_activation(action)

    def activate(self, action: actions.ItemAction) -> None:
        target = action.target_actor

        if not target:
            self.engine.message_log.add_message("The projectile dissipates in the air.",color.grey)

        if target:
            self.engine.message_log.add_message(
                f"The eyes of the {target.name} glaze over as it stumbles about",
                color.offwhite,
            )
            target.ai = basilisk.components.ai.ConfusedEnemy(
                entity=target, previous_ai=target.ai, turns_remaining=self.number_of_turns,
            )


class MappingConsumable(Consumable):
    description = "map this floor"

    def activate(self, action: actions.ItemAction) -> None:
        self.engine.message_log.add_message("Your mind permeates the walls of the dungeon.")
        self.engine.game_map.make_mapped()


class LightningDamageConsumable(Projectile):
    description = "smite a random enemy"

    def __init__(self, damage: int=4, maximum_range: int=5):
        self.damage = damage
        self.maximum_range = maximum_range
        self.do_snake = False

    def activate(self, action: actions.ItemAction) -> None:
        consumer = action.entity
        target = None
        closest_distance = self.maximum_range + 1.0

        for actor in self.engine.game_map.actors:
            if actor is not consumer and self.parent.gamemap.visible[actor.x, actor.y]:
                distance = consumer.distance(actor.x, actor.y)

                if distance < closest_distance:
                    target = actor
                    closest_distance = distance

        if target:
            self.engine.message_log.add_message(
                f"Lightning smites the {target.name} for {self.modified_damage} damage!", color.offwhite,
            )
            target.take_damage(self.modified_damage)
        else:
            self.engine.message_log.add_message(f"Lightning strikes the ground nearby.", color.offwhite)


class FireballDamageConsumable(Projectile):
    description = "conjure an explosion"

    def __init__(self, damage: int=2, radius: int=2):
        self.damage = damage
        self.radius = radius
        self.do_snake = False

    def get_throw_action(self, consumer: Actor) -> AreaRangedAttackHandler:
        if not self.parent.identified:
            return super().get_throw_action(consumer)
            
        self.engine.message_log.add_message(
            "Select a target location.", color.cyan
        )
        return AreaRangedAttackHandler(
            self.engine,
            radius=self.radius,
            callback=lambda xy: actions.ThrowItem(consumer, self.parent, xy),
        )

    def start_activation(self,action):
        if not self.engine.game_map.visible[action.target_xy]:
            raise Impossible("You cannot target an area that you cannot see.")
        super().start_activation(action)

    def activate(self, action: actions.ItemAction) -> None:
        target_xy = action.target_xy

        targets_hit = False
        for entity in list(self.engine.game_map.entities)[:]:
            if entity.distance(*target_xy) <= self.radius:
                self.engine.message_log.add_message(
                    f"The explosion engulfs the {entity.label}! It takes {self.modified_damage} damage!", color.offwhite,
                )
                entity.take_damage(self.modified_damage)
                targets_hit = True

        if not targets_hit:
            self.engine.message_log.add_message("The explosion echoes through the dungeon.")


